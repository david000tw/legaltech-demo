"""AWS Bedrock 日本區（Japan 跨區設定檔，資料留在東京／大阪）。

已預留介面，尚未啟用：試用與正式上線前，於計畫 5 以 AnthropicBedrockMantle 實作並做雙平台回歸。
"""
from app.ai.provider import AIResult, ProviderNotEnabled


class BedrockProvider:
    def run(self, *, step: str, system: str, user: str, schema: dict, effort: str,
            timeout_s: int = 600) -> AIResult:
        raise ProviderNotEnabled("Bedrock 尚未啟用")
