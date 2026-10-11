"""统一截图、定位、输入和等待验证的控制器。"""

import time
from math import isfinite
from pathlib import Path
from typing import Literal

from loguru import logger

from wemo2_collector.interfaces import (
    BaseController,
    BaseDriver,
    BaseLocator,
    BaseScreenshotter,
    BaseValidator,
)
from wemo2_collector.models import (
    ActionResult,
    ControllerConfig,
    Direction,
    Expectation,
    LocatedTarget,
    Screenshot,
)


class UiActionError(RuntimeError):
    """向路由/调试入口传播失败，同时保留结构化结果。"""

    def __init__(self, result: ActionResult):
        self.result = result
        super().__init__(result.error or "界面动作失败")


class UiController(BaseController):
    """统一执行截图、定位、单次点击或滑动，以及操作后的等待和校验。

    只读步骤允许有限重试；输入动作不自动重发，始终保留执行状态和证据。
    不负责页面路径规划、动态解析或数据去重。
    """

    def __init__(
        self,
        driver: BaseDriver,
        screenshotter: BaseScreenshotter,
        locator: BaseLocator,
        validator: BaseValidator,
        config: ControllerConfig | None = None,
        directory: Path | None = None,
    ):
        self.driver = driver
        self.screenshotter = screenshotter
        self.locator = locator
        self.validator = validator
        self.config = config or ControllerConfig()
        self.directory = directory
        self._artifacts: list[Path] = []
        self.results: list[ActionResult] = []

    def reset(self) -> None:
        self._artifacts = []
        self.results = []

    def capture(self, *, follow_foreground: bool = False) -> Screenshot:
        for attempt in range(self.config.capture_attempts):
            try:
                if follow_foreground:
                    self.driver.follow_foreground()
                surface = self.driver.prepare_capture()
                frame = self.screenshotter.capture(surface)
                # 即使校验失败，也保留截图路径作为诊断证据。
                self._artifacts.append(frame.image_path)
                self.driver.verify_capture(surface)
                self.validator.validate_frame(frame)
                return frame
            except (OSError, RuntimeError, ValueError) as error:
                logger.exception(
                    "截图或校验失败，第 {}/{} 次：{}",
                    attempt + 1,
                    self.config.capture_attempts,
                    error,
                )
                if attempt + 1 == self.config.capture_attempts:
                    raise
                time.sleep(self.config.poll_interval)
        raise RuntimeError("截图失败")

    def locate(
        self, name: str, frame: Screenshot | None = None
    ) -> LocatedTarget | None:
        if frame is not None:
            self.validator.validate_frame(frame)
            target = self.locator.locate(frame, name)
            if target is not None:
                self.validator.validate_target(frame, target)
            return target
        for attempt in range(self.config.locate_attempts):
            fresh = self.capture()
            target = self.locate(name, fresh)
            if target is not None:
                return target
            if attempt + 1 < self.config.locate_attempts:
                time.sleep(self.config.poll_interval)
        return None

    def detect_page(self, frame: Screenshot) -> str:
        return self.validator.detect_page(frame)

    def click(
        self,
        name: str,
        expectation: Expectation | None = None,
        *,
        source_page: str | None = None,
        timeout: float | None = None,
        settle_seconds: float = 0.2,
        follow_foreground: bool = False,
    ) -> ActionResult:
        return self._perform(
            "click",
            name,
            expectation,
            source_page=source_page,
            timeout=timeout,
            settle_seconds=settle_seconds,
            follow_foreground=follow_foreground,
        )

    def swipe(
        self,
        name: str,
        direction: Direction,
        distance: int,
        expectation: Expectation | None = None,
        *,
        timeout: float | None = None,
        settle_seconds: float = 0.2,
    ) -> ActionResult:
        return self._perform(
            "swipe",
            name,
            expectation,
            direction=direction,
            distance=distance,
            timeout=timeout,
            settle_seconds=settle_seconds,
        )

    def _prepare(
        self, result: ActionResult, source_page: str | None
    ) -> tuple[Screenshot, LocatedTarget]:
        for attempt in range(self.config.locate_attempts):
            frame = self.capture()
            result.before = frame
            if source_page is not None and self.detect_page(frame) != source_page:
                raise RuntimeError(f"动作前页面不再是 {source_page}，未执行点击")
            target = self.locate(result.name, frame)
            if target is not None:
                result.target = target
                return frame, target
            if attempt + 1 < self.config.locate_attempts:
                time.sleep(self.config.poll_interval)
        raise RuntimeError(f"目标 {result.name} 未可靠识别或匹配不唯一，未执行动作")

    def _perform(
        self,
        operation: Literal["click", "swipe"],
        name: str,
        expectation: Expectation | None,
        *,
        source_page: str | None = None,
        direction: Direction | None = None,
        distance: int | None = None,
        timeout: float | None = None,
        settle_seconds: float = 0.2,
        follow_foreground: bool = False,
    ) -> ActionResult:
        result = ActionResult(operation=operation, name=name, expectation=expectation)
        evidence_start = len(self._artifacts)
        timeout = self.config.verification_timeout if timeout is None else timeout
        try:
            if (
                not isfinite(timeout)
                or not isfinite(settle_seconds)
                or timeout <= 0
                or timeout > 60
                or settle_seconds < 0
                or settle_seconds > 10
            ):
                raise ValueError(
                    "动作超时必须在 (0, 60] 秒内，重绘等待必须在 [0, 10] 秒内"
                )
            if operation == "swipe" and (
                direction not in ("up", "down", "left", "right")
                or type(distance) is not int
                or distance <= 0
            ):
                raise ValueError("滑动方向无效或距离不是正整数像素")
            if operation == "swipe":
                result.direction = direction
                result.distance = distance
            before, target = self._prepare(result, source_page)
            if expectation is not None:
                # 先确认验证规则和区域合法，不能输入后才发现配置错误。
                self.validator.evaluate(before, before, expectation)
            self.validator.validate_target(before, target)
            self.driver.verify_capture(before.surface)
            result.attempted = True
            if operation == "click":
                self.driver.click(before, target)
            else:
                self.driver.swipe(before, target, direction, distance)
            result.executed = True
            result.status = "executed"
            time.sleep(settle_seconds)
            self._observe(result, timeout, follow_foreground)
        except Exception as error:
            logger.exception(
                "界面动作 {} 失败；已尝试 {}，已执行 {}：{}",
                operation,
                result.attempted,
                result.executed,
                error,
            )
            result.status = "failed"
            result.error = str(error)
        result.evidence = self._artifacts[evidence_start:]
        return self._finish(result)

    def _observe(
        self, result: ActionResult, timeout: float, follow_foreground: bool
    ) -> None:
        deadline = time.monotonic() + timeout
        while True:
            after = self.capture(follow_foreground=follow_foreground)
            result.after = after
            if result.expectation is None:
                return  # 只有平台执行事实，没有页面成功结论。
            validation = self.validator.evaluate(
                result.before, after, result.expectation
            )
            result.validation = validation
            if validation.status == "passed":
                result.status = "verified"
                return
            if validation.status == "unavailable":
                result.status = "pending"
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError(
                    f"动作后验证超时：{validation.message}；不会重复输入"
                )
            time.sleep(min(self.config.poll_interval, remaining))

    def _finish(self, result: ActionResult) -> ActionResult:
        if self.directory is not None:
            try:
                self.directory.mkdir(parents=True, exist_ok=True)
                path = (self.directory / f"action_{result.action_id}.json").resolve()
                path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
                self._artifacts.append(path)
            except OSError as error:
                logger.exception(
                    "保存动作证据失败，动作 {}：{}", result.action_id, error
                )
                result.status = "failed"
                result.error = f"无法保存动作证据：{error}；原始错误：{result.error}"
        self.results.append(result)
        logger.info(
            "动作 {}：{}，已尝试 {}，已执行 {}",
            result.name,
            result.status,
            result.attempted,
            result.executed,
        )
        return result

    def artifacts(self) -> list[Path]:
        return list(self._artifacts)
