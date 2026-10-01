"""轻量重排序服务（Reranker）：对双路混合召回的候选集用交叉编码器做二次精排

设计要点（贴合金融检索「精准优先」与防假死约定）：
    - 交叉编码器 (query, doc) 逐对打分，判别力显著强于双塔的余弦相似度；
    - 懒加载：首次调用才实例化模型，纯向量/未开启精排时不承担加载成本；
    - 优雅降级：模型缺失或加载失败时 available=False，上层自动回退双路加权排序，
      绝不抛异常阻断检索链路；
    - 离线优先：导入 src.data.config 即已触发 src.config 设置 HF_HUB_OFFLINE，
      模型已在本地/HF 缓存时不联网校验，避免首假死。
"""
from src.data.config import RERANKER_MODEL_PATH


class RerankService:
    _instance = None
    _model = None
    _loaded = False
    _available = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def _ensure_model(self) -> bool:
        """首次使用时加载 CrossEncoder；失败仅记警告并置不可用，返回可用性"""
        if self._loaded:
            return self._available
        self._loaded = True
        try:
            # 延迟导入：仅精排启用时才引入 sentence_transformers 依赖
            from sentence_transformers import CrossEncoder

            self._model = CrossEncoder(RERANKER_MODEL_PATH)
            self._available = True
            print(f"Reranker 模型加载完成：{RERANKER_MODEL_PATH}")
        except Exception as e:
            self._model = None
            self._available = False
            print(f"[警告] Reranker 加载失败，检索自动回退双路加权排序：{e}")
        return self._available

    @property
    def available(self) -> bool:
        return self._ensure_model()

    def scores(self, query: str, docs: list[str]) -> list[float]:
        """对 (query, doc) 逐对打分，返回与 docs 同序的相关性得分；不可用时返回空表"""
        if not self.available or not docs:
            return []
        raw = self._model.predict([(query, d) for d in docs])
        return [float(s) for s in raw]


# 全局实例（创建廉价，模型按需懒加载）
reranker_service = RerankService()
