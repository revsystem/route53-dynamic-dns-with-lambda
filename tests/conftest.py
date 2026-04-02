import sys
import os

# lambda/ ディレクトリを sys.path に追加して Lambda コードをインポート可能にする
# "lambda" は Python 予約語のため、パッケージとしてインポートできない
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lambda"))
