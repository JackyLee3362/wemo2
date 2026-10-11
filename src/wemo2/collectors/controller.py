"""统一截图、定位、输入和等待验证的控制器。"""

import time
from collections.abc import Callable
from math import isfinite
from pathlib import Path
from typing import Literal

from loguru import logger
from PIL import Image

from wemo2.collectors import validation
from wemo2.collectors.interfaces import BaseDriver
from wemo2.collectors.locating import AirtestLocator
from wemo2.collectors.models import (
    ActionResult,
    ControllerConfig,
    Direction,
    Expectation,
    LocatedTarget,
    Rect,
    Screenshot,
    ValidationResult,
)
from wemo2.collectors.profiles import MediaStepSpec, UiProfile
from wemo2.collectors.recognition import TextRecognizer
from wemo2.collectors.windows.screenshot import WindowsScreenshotter


class UiActionError(RuntimeError):
    """向路由/调试入口传播失败，同时保留结构化结果。"""

    def __init__(self, result: ActionResult):
        self.result = result
        super().__init__(result.error or "界面动作失败")


class UiController:
    """统一执行截图、定位、单次点击或滑动，以及操作后的等待和校验。

    只读步骤允许有限重试；输入动作不自动重发，始终保留执行状态和证据。
    不负责页面路径规划、动态解析或数据去重。
    """

    def __init__(
        self,
        driver: BaseDriver,
        screenshotter: WindowsScreenshotter,
        locator: AirtestLocator,
        profile: UiProfile,
        config: ControllerConfig | None = None,
        directory: Path | None = None,
    ):
        self.driver = driver
        self.screenshotter = screenshotter
        self.locator = locator
        self.profile = profile
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
                validation.validate_frame(frame, self.config.max_frame_age)
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
            validation.validate_frame(frame, self.config.max_frame_age)
            target = self.locator.locate(frame, name)
            if target is not None:
                validation.validate_target(frame, target, self.config.max_frame_age)
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
        return validation.detect_page(frame, self.profile, self.locator)

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
        self,
        result: ActionResult,
        source_page: str | None,
        region: Rect | None = None,
        locate_target: Callable[[Screenshot], LocatedTarget | None] | None = None,
    ) -> tuple[Screenshot, LocatedTarget]:
        for attempt in range(self.config.locate_attempts):
            frame = self.capture()
            result.before = frame
            if source_page is not None and self.detect_page(frame) != source_page:
                raise RuntimeError(f"动作前页面不再是 {source_page}，未执行点击")
            if locate_target is not None:
                target = locate_target(frame)
            elif region is None:
                target = self.locate(result.name, frame)
            else:
                target = LocatedTarget(
                    frame_id=frame.frame_id,
                    name=result.name,
                    bounds=region,
                    score=1,
                    method="configured_region",
                )
            if target is not None:
                result.target = target
                return frame, target
            if attempt + 1 < self.config.locate_attempts:
                time.sleep(self.config.poll_interval)
        raise RuntimeError(f"目标 {result.name} 未可靠识别或匹配不唯一，未执行动作")

    def _perform(
        self,
        operation: Literal["click", "right_click", "swipe", "scroll"],
        name: str,
        expectation: Expectation | None,
        *,
        source_page: str | None = None,
        direction: Direction | None = None,
        distance: int | None = None,
        timeout: float | None = None,
        settle_seconds: float = 0.2,
        follow_foreground: bool = False,
        region: Rect | None = None,
        wheel_steps: int | None = None,
        locate_target: Callable[[Screenshot], LocatedTarget | None] | None = None,
        verify: Callable[[Screenshot], ValidationResult] | None = None,
        workflow_name: str | None = None,
        stable_region: Rect | None = None,
    ) -> ActionResult:
        result = ActionResult(
            operation=operation,
            name=name,
            expectation=expectation,
            workflow=workflow_name,
        )
        logger.info(
            "[动作] 开始 {} / {}，动作 ID={}", operation, name, result.action_id
        )
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
            if operation == "scroll":
                if type(wheel_steps) is not int or not 1 <= wheel_steps <= 10:
                    raise ValueError("滚轮步数必须为 1 到 10 的整数")
                result.wheel_steps = wheel_steps
            before, target = self._prepare(result, source_page, region, locate_target)
            logger.info(
                "[定位] {}，坐标=({}, {})，范围={}×{}",
                name,
                target.bounds.x,
                target.bounds.y,
                target.bounds.width,
                target.bounds.height,
            )
            if operation == "scroll" and region is not None:
                static_region = self.profile.feed_static_bounds(
                    region, before.surface.scale
                )
                expectation = Expectation(kind="change", region=static_region)
                result.expectation = expectation
                stable_region = static_region
            if expectation is not None:
                # 先确认验证规则和区域合法，不能输入后才发现配置错误。
                validation.evaluate(
                    before, before, expectation, self.profile, self.locator
                )
            validation.validate_target(before, target, self.config.max_frame_age)
            self.driver.verify_capture(before.surface)
            result.attempted = True
            if operation == "click":
                self.driver.click(before, target)
            elif operation == "right_click":
                self.driver.right_click(before, target)
            elif operation == "scroll":
                logger.info("[滚动] 发送一次滚轮输入，步数={}", wheel_steps)
                self.driver.scroll(before, target, wheel_steps)
            else:
                logger.info(
                    "[滑动] 发送一次拖动输入，方向={}，距离={} 像素",
                    direction,
                    distance,
                )
                self.driver.swipe(before, target, direction, distance)
            result.executed = True
            result.status = "executed"
            time.sleep(settle_seconds)
            self._observe(result, timeout, follow_foreground, verify)
            if stable_region is not None and result.status == "verified":
                self._wait_stable(result, stable_region, timeout)
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
        self,
        result: ActionResult,
        timeout: float,
        follow_foreground: bool,
        verify: Callable[[Screenshot], ValidationResult] | None = None,
    ) -> None:
        deadline = time.monotonic() + timeout
        while True:
            after = self.capture(follow_foreground=follow_foreground)
            result.after = after
            if verify is not None:
                checked = verify(after)
            elif result.expectation is None:
                return  # 只有平台执行事实，没有页面成功结论。
            else:
                checked = validation.evaluate(
                    result.before, after, result.expectation, self.profile, self.locator
                )
            result.validation = checked
            logger.debug(
                "[校验] {}，状态={}，{}", result.name, checked.status, checked.message
            )
            if checked.status == "passed":
                result.status = "verified"
                return
            if checked.status == "unavailable":
                result.status = "pending"
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError(f"动作后验证超时：{checked.message}；不会重复输入")
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
        message = "未配置后置校验"
        if result.validation is not None:
            message = result.validation.message
        logger.info(
            "[动作] 完成 {} / {}：{}，已尝试={}，已执行={}，校验={}，动作 ID={}",
            result.operation,
            result.name,
            result.status,
            result.attempted,
            result.executed,
            message,
            result.action_id,
        )
        return result

    def artifacts(self) -> list[Path]:
        return list(self._artifacts)

    def scroll(
        self,
        region: Rect,
        steps: int,
        *,
        settle_seconds: float = 0.6,
        timeout: float = 2.0,
    ) -> ActionResult:
        """滚动一次，只观察头像侧栏的移动和稳定，视频播放不计作滚动进展。"""
        return self._perform(
            "scroll",
            "feed_region",
            Expectation(kind="change", region=region),
            region=region,
            wheel_steps=steps,
            timeout=timeout,
            settle_seconds=settle_seconds,
            stable_region=region,
        )

    def _wait_stable(self, result: ActionResult, region: Rect, timeout: float) -> None:
        previous = result.after

        def stable(frame: Screenshot) -> ValidationResult:
            nonlocal previous
            passed = previous is not None and validation.region_unchanged(
                previous, frame, region
            )
            previous = frame
            return ValidationResult(
                status="passed" if passed else "not_met",
                message="滚动后列表连续两帧稳定",
            )

        self._observe(result, timeout, False, stable)

    def click_region(
        self,
        name: str,
        region: Rect,
        *,
        right: bool = False,
        follow_foreground: bool = False,
    ) -> ActionResult:
        """在当前截图中已确认的媒体/文字范围输入一次；调用者需验证内容连续性。"""
        operation = "right_click" if right else "click"
        return self._perform(
            operation,
            name,
            None,
            region=region,
            follow_foreground=follow_foreground,
            settle_seconds=0.6,
        )

    def platform_step(
        self,
        operation: Literal["save_file", "close_viewer", "switch_image"],
        action: Callable,
        *,
        name: str | None = None,
        before: Callable[[Screenshot], None] | None = None,
        verify_file: Callable[[Path], None] | None = None,
        verify: Callable[[Screenshot], ValidationResult] | None = None,
        timeout: float = 5,
        workflow_name: str | None = None,
        settle_seconds: float = 0,
        direction: Direction | None = None,
    ) -> object:
        """记录一次平台对话框操作，失败保留执行事实，不自动重发。"""
        result = ActionResult(
            operation=operation,
            name=name or operation,
            workflow=workflow_name,
            direction=direction,
        )
        logger.info(
            "[动作] 开始 {} / {}，动作 ID={}", operation, result.name, result.action_id
        )
        start = len(self._artifacts)
        value = None
        try:
            result.before = self.capture(follow_foreground=True)
            if before is not None:
                before(result.before)
            result.attempted = True
            value = action()
            result.executed = True
            result.status = "executed"
            if isinstance(value, Path) and value.is_file():
                self._artifacts.append(value)
            if verify_file is not None:
                if not isinstance(value, Path):
                    raise RuntimeError("保存步骤未返回文件路径")
                verify_file(value)
            time.sleep(settle_seconds)
            self._observe(result, timeout, True, verify)
        except Exception as error:
            logger.exception("媒体平台步骤 {} 失败，停止后续输入：{}", operation, error)
            result.error = str(error)
            result.status = "failed"
        result.evidence = self._artifacts[start:]
        self._finish(result)
        if result.status == "failed":
            raise UiActionError(result)
        return value

    def _media_target(
        self,
        frame: Screenshot,
        name: str,
        source: Screenshot,
        bounds: Rect,
        reader: TextRecognizer,
    ) -> LocatedTarget | None:
        """解析配置中的动态、相对区域或文字目标，所有结果属于当前帧。"""
        spec = self.profile.media_target(name)
        if spec.kind == "source":
            if frame.surface != source.surface:
                raise RuntimeError("媒体定位后列表窗口发生变化，未点击")
            if not validation.region_unchanged(source, frame, bounds):
                raise RuntimeError("媒体内容已变化，未使用旧坐标点击")
            area = bounds
        else:
            x, y, width, height = spec.region
            size = frame.surface.bounds
            left = min(size.width - 1, round(size.width * x))
            top = min(size.height - 1, round(size.height * y))
            area = Rect(
                x=left,
                y=top,
                width=min(size.width - left, max(1, round(size.width * width))),
                height=min(size.height - top, max(1, round(size.height * height))),
            )
        if spec.kind != "ocr":
            return LocatedTarget(
                frame_id=frame.frame_id,
                name=name,
                bounds=area,
                score=1,
                method="configured_region",
            )
        with Image.open(frame.image_path) as image:
            with image.crop(
                (area.x, area.y, area.x + area.width, area.y + area.height)
            ) as crop:
                lines = reader.read(crop)
        matches = []
        for line in lines:
            text = line.text.replace(" ", "").rstrip(".。…")
            for candidate in spec.texts:
                if line.score >= spec.threshold and text == candidate.replace(
                    " ", ""
                ).rstrip(".。…"):
                    matches.append(line)
                    break
        if len(matches) != 1:
            return None
        line = matches[0]
        return LocatedTarget(
            frame_id=frame.frame_id,
            name=name,
            bounds=Rect(
                x=area.x + line.bounds.x,
                y=area.y + line.bounds.y,
                width=line.bounds.width,
                height=line.bounds.height,
            ),
            score=line.score,
            method="ocr",
        )

    def switch_image(
        self,
        source: Screenshot,
        bounds: Rect,
        viewer: Screenshot,
        *,
        direction: Literal["left", "right"] = "right",
        workflow_name: str = "save_media",
    ) -> Screenshot:
        """发送一次方向键并验证目标缩略图的放大版本，失败交由调用者选择兜底。"""
        workflow = self.profile.media_workflow(workflow_name)
        current = viewer

        def before(frame: Screenshot) -> None:
            nonlocal current
            if frame.surface != viewer.surface or self.driver.is_save_dialog():
                raise RuntimeError("图片切换前查看器窗口已变化，未发送方向键")
            current = frame

        def verify(frame: Screenshot) -> ValidationResult:
            passed = (
                frame.surface == viewer.surface
                and self.locator.is_enlarged_preview(
                    source,
                    frame,
                    bounds,
                    workflow.viewer_region,
                    workflow.viewer_threshold,
                    workflow.viewer_min_scale,
                    allow_independent=True,
                )
            )
            return ValidationResult(
                status="passed" if passed else "not_met",
                message="方向键切换后的目标图片内容校验",
            )

        self.platform_step(
            "switch_image",
            lambda: self.driver.switch_image(current, direction),
            before=before,
            verify=verify,
            timeout=self.config.verification_timeout,
            workflow_name=workflow_name,
            settle_seconds=0.6,
            direction=direction,
        )
        frame = self.results[-1].after
        if frame is None:
            raise RuntimeError("图片切换后未交付截图")
        return frame

    def close_media_viewer(
        self,
        source: Screenshot,
        bounds: Rect,
        viewer: Screenshot,
        reader: TextRecognizer,
        *,
        workflow_name: str = "save_media",
        alternate_bounds: Rect | None = None,
    ) -> None:
        """关闭已保存图片的查看器并验证原列表；只允许退出本次确认的查看器。"""
        workflow = self.profile.media_workflow(workflow_name)

        def before(frame: Screenshot) -> None:
            if frame.surface != viewer.surface or self.driver.is_save_dialog():
                raise RuntimeError("图片查看器状态已变化，不能自动退出")
            if frame.surface == source.surface:
                candidates = [bounds]
                if alternate_bounds is not None:
                    candidates.append(alternate_bounds)
                for candidate in candidates:
                    if self.locator.is_enlarged_preview(
                        source,
                        frame,
                        candidate,
                        workflow.viewer_region,
                        workflow.viewer_threshold,
                        workflow.viewer_min_scale,
                    ):
                        return
                raise RuntimeError("未确认内嵌图片查看器，不能自动退出")

        def verify(frame: Screenshot) -> ValidationResult:
            passed = frame.surface == source.surface
            if passed:
                self._media_target(
                    frame, workflow.steps[workflow.entry].target, source, bounds, reader
                )
            return ValidationResult(
                status="passed" if passed else "not_met",
                message="图片批次结束或兜底前返回原列表",
            )

        self.platform_step(
            "close_viewer",
            lambda: self.driver.close_viewer(
                source.surface, allow_embedded=viewer.surface == source.surface
            ),
            before=before,
            verify=verify,
            workflow_name=workflow_name,
            settle_seconds=0.6,
        )

    def run_media_workflow(
        self,
        source: Screenshot,
        bounds: Rect,
        directory: Path,
        reader: TextRecognizer,
        on_file: Callable[[Path], None],
        *,
        workflow_name: str = "save_media",
        load_wait_seconds: float = 5,
        load_timeout_seconds: float = 30,
        start_in_viewer: Screenshot | None = None,
        keep_viewer: Callable[[], bool] | None = None,
    ) -> Path:
        """执行已校验的媒体步骤图；每次输入仅执行一次，验证失败即停止并留证。"""
        self.profile.validate_structure()
        workflow = self.profile.media_workflow(workflow_name)
        path = None

        def check(frame: Screenshot, state: str) -> ValidationResult:
            if state == "menu":
                matched = self._media_target(
                    frame, workflow.menu_target, source, bounds, reader
                )
                passed = matched is not None
                message = "另存为菜单已验证" if passed else "未唯一识别到媒体菜单目标"
            elif state == "save_dialog":
                passed = self.driver.is_save_dialog()
                message = "另存为对话框状态"
            elif state == "original":
                passed = frame.surface == source.surface
                if passed:
                    self._media_target(
                        frame,
                        workflow.steps[workflow.entry].target,
                        source,
                        bounds,
                        reader,
                    )
                message = "返回原列表及内容连续性"
            else:
                passed = False
                if not self.driver.is_save_dialog():
                    if start_in_viewer is not None:
                        passed = (
                            frame.surface == start_in_viewer.surface
                            and self.locator.is_enlarged_preview(
                                source,
                                frame,
                                bounds,
                                workflow.viewer_region,
                                workflow.viewer_threshold,
                                workflow.viewer_min_scale,
                                allow_independent=True,
                            )
                        )
                    elif workflow.viewer_mode in ("auto", "independent"):
                        passed = frame.surface.surface_id != source.surface.surface_id
                    if (
                        start_in_viewer is None
                        and not passed
                        and workflow.viewer_mode in ("auto", "embedded")
                    ):
                        passed = self.locator.is_enlarged_preview(
                            source,
                            frame,
                            bounds,
                            workflow.viewer_region,
                            workflow.viewer_threshold,
                            workflow.viewer_min_scale,
                        )
                message = "独立窗口或原缩略图放大后的媒体查看器状态"
            return ValidationResult(
                status="passed" if passed else "not_met", message=message
            )

        def require(frame: Screenshot, state: str) -> None:
            checked = check(frame, state)
            if checked.status != "passed":
                raise RuntimeError(f"媒体步骤前置状态不满足：{checked.message}")

        def execute(name: str, step: MediaStepSpec) -> object:
            logger.info(
                "[媒体流程] {} / {}，动作={}，前置={}，后置={}",
                workflow_name,
                name,
                step.action,
                step.before,
                step.expect,
            )

            def before(frame: Screenshot) -> None:
                require(frame, step.before)

            def verify(frame: Screenshot) -> ValidationResult:
                state = "viewer" if step.expect == "file" else step.expect
                return check(frame, state)

            if step.action in ("click", "right_click"):

                def locate(frame: Screenshot) -> LocatedTarget | None:
                    before(frame)
                    return self._media_target(
                        frame, step.target, source, bounds, reader
                    )

                result = self._perform(
                    step.action,
                    name,
                    None,
                    locate_target=locate,
                    verify=verify,
                    timeout=step.timeout_seconds,
                    settle_seconds=step.settle_seconds,
                    follow_foreground=True,
                    workflow_name=workflow_name,
                )
                if result.status != "verified":
                    raise UiActionError(result)
            elif step.action == "save_file":
                return self.platform_step(
                    "save_file",
                    lambda: self.driver.save_dialog(directory),
                    name=name,
                    before=before,
                    verify_file=on_file,
                    verify=verify,
                    timeout=step.timeout_seconds,
                    workflow_name=workflow_name,
                    settle_seconds=step.settle_seconds,
                )
            elif step.action == "wait_ready":
                logger.info(
                    "[媒体加载] 先等待 {} 秒，总等待上限 {} 秒，加载校验通过后再右键",
                    load_wait_seconds,
                    load_timeout_seconds,
                )
                result = ActionResult(
                    operation="wait_ready", name=name, workflow=workflow_name
                )
                start = len(self._artifacts)
                try:
                    result.before = self.capture(follow_foreground=True)
                    before(result.before)
                    time.sleep(load_wait_seconds)

                    def ready(frame: Screenshot) -> ValidationResult:
                        checked = verify(frame)
                        if checked.status != "passed":
                            return checked
                        with Image.open(frame.image_path) as image:
                            lines = reader.read(image)
                        for line in lines:
                            text = line.text.replace(" ", "").lower()
                            if any(
                                word in text
                                for word in (
                                    "加载中",
                                    "正在加载",
                                    "下载中",
                                    "正在下载",
                                    "loading",
                                    "downloading",
                                )
                            ):
                                return ValidationResult(
                                    status="not_met",
                                    message="媒体仍在加载，继续等待；尚未右键",
                                )
                        return ValidationResult(
                            status="passed", message="媒体已等待加载，未观察到加载提示"
                        )

                    self._observe(
                        result, load_timeout_seconds - load_wait_seconds, True, ready
                    )
                except Exception as error:
                    logger.exception("媒体加载等待失败，停止后续右键和保存：{}", error)
                    result.status = "failed"
                    result.error = str(error)
                result.evidence = self._artifacts[start:]
                self._finish(result)
                if result.status != "verified":
                    raise UiActionError(result)
            else:

                def close_viewer() -> None:
                    current_surface = self.driver.current_surface()
                    if current_surface.surface_id == source.surface.surface_id:
                        # before 已验证放大图；按 Escape 后仍须验证恢复原预览。
                        self.driver.close_viewer(source.surface, allow_embedded=True)
                    else:
                        self.driver.close_viewer(source.surface)

                self.platform_step(
                    "close_viewer",
                    close_viewer,
                    name=name,
                    before=before,
                    verify=verify,
                    timeout=step.timeout_seconds,
                    workflow_name=workflow_name,
                    settle_seconds=step.settle_seconds,
                )
                return None

        current = workflow.entry
        if start_in_viewer is not None:
            current = workflow.steps[current].next
        for _ in range(workflow.max_steps):
            if current is None:
                raise RuntimeError("媒体流程没有查看器保存步骤")
            step = workflow.steps[current]
            if (
                step.action == "close_viewer"
                and keep_viewer is not None
                and keep_viewer()
            ):
                if path is None:
                    raise RuntimeError("尚未保存文件，不能保留查看器进入下一张")
                return path  # 保存和查看器已验证，退出由图片批次策略负责留证。
            value = execute(current, step)
            if isinstance(value, Path):
                path = value
            current = step.next
            if current is None:
                if not isinstance(path, Path):
                    raise RuntimeError("媒体流程结束但未交付文件路径")
                return path
        raise RuntimeError("媒体流程超出步骤上限")
