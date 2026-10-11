"""同一条朋友圈内的图片保存策略；失败兜底只针对方向键切换。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from loguru import logger
from pydantic import BaseModel, ConfigDict

from wemo2_collector.controller import UiActionError
from wemo2_collector.models import Rect, Screenshot

if TYPE_CHECKING:
    from wemo2_collector.media import CardCapture, MediaCapture


class ViewerSession(BaseModel):
    """本条朋友圈的已验证查看器与待确认缓存范围，不跨卡片复用。"""

    model_config = ConfigDict(extra="forbid")
    source: Screenshot
    bounds: Rect
    viewer: Screenshot
    preview_start: int


class ImageSaveStrategy(Protocol):
    """保存本卡片候选媒体的共同契约；输入和文件校验仍由控制器负责。"""

    def save(
        self,
        capture: MediaCapture,
        card: CardCapture,
        targets: list[Rect],
        stream_offset: int,
        region: Rect,
    ) -> None:
        """保存指定卡片媒体，立即将已完成的文件关联至卡片证据。"""


class ReopenImageStrategy:
    """逐张退出查看器并重新点击缩略图，也是方向键切换失败后的兜底。"""

    def save(
        self,
        capture: MediaCapture,
        card: CardCapture,
        targets: list[Rect],
        stream_offset: int,
        region: Rect,
    ) -> None:
        """沿用逐图保存流程，不猜测查看器下一张是否属于当前朋友圈。"""
        for target in targets:
            card.media.append(
                capture._save_media(
                    card.source, target, stream_offset + target.y - region.y
                )
            )


class ArrowImageStrategy:
    """图片优先在查看器中用方向键连续保存；视频仍采用独立保存流程。"""

    def save(
        self,
        capture: MediaCapture,
        card: CardCapture,
        targets: list[Rect],
        stream_offset: int,
        region: Rect,
    ) -> None:
        """只处理本卡片可见候选，切换失败后验证退出并对剩余候选启用兜底。"""
        for index, target in enumerate(targets):
            session = capture.viewer
            if session is not None:
                try:
                    session.viewer = capture.controller.switch_image(
                        card.source,
                        target,
                        session.viewer,
                        workflow_name=capture.config.media_workflow,
                    )
                except UiActionError as error:
                    logger.exception(
                        "图片方向键切换失败，验证返回列表后改用逐张重开；已保存文件保留：{}",
                        error,
                    )
                    capture.close_viewer(alternate_bounds=target)
                    ReopenImageStrategy().save(
                        capture, card, targets[index:], stream_offset, region
                    )
                    return
            card.media.append(
                capture._save_media(
                    card.source,
                    target,
                    stream_offset + target.y - region.y,
                    keep_viewer=True,
                )
            )
        capture.close_viewer()


def image_save_strategy(use_arrows: bool) -> ImageSaveStrategy:
    """在采集包内集中选择两种已实现策略，应用层不感知查看器细节。"""
    if use_arrows:
        return ArrowImageStrategy()
    return ReopenImageStrategy()
