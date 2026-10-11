"""内存存储替身，演示保存流程，不连接尚未实现的 Memos。"""

from loguru import logger

from wemo2.api.interfaces import BaseStore
from wemo2.api.models import ArchivedPost


class PseudoStore(BaseStore):
    """在内存中接收归档与资源引用，演示未来 Memos 0.31 保存接口，不上传数据。"""

    def __init__(self):
        self.posts: list[ArchivedPost] = []

    def save(self, post: ArchivedPost) -> None:
        """接收一条已校验的朋友圈及其实际文件，不再做人物文案去重。"""
        self.posts.append(post.model_copy(deep=True))
        logger.info(
            "模拟保存朋友圈：账号={}，人物={}，日期={}，媒体={}，源目录={}",
            post.record.account,
            post.record.person,
            post.record.approximate_date,
            len(post.media_files),
            post.metadata_path.parent,
        )
