"""朋友圈入口路由的独立调试入口。"""

from pathlib import Path

from wemo2_collector.main import main

if __name__ == "__main__":
    main(Path(__file__).resolve().parents[2] / "wemo2.moments.toml")
