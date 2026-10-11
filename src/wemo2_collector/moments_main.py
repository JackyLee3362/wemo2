"""朋友圈导航、滚动截图、昵称及媒体采集的独立调试入口。"""

from pathlib import Path

from wemo2_collector.main import main

if __name__ == "__main__":
    main(Path(__file__).resolve().parents[2] / "wemo2.moments.toml")
