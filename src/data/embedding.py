"""BGE 中文向量服务：查询侧加检索指令前缀，文档侧不加"""
from sentence_transformers import SentenceTransformer

from src.data.config import EMBEDDING_MODEL_PATH


class EmbeddingService:
    _instance = None
    _model = None
    # BGE 中文检索固定指令前缀（仅查询用，文档不需要加）
    QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            # 本地部署目录优先（models/bge-base-zh-v1.5），无本地目录时回退 HF 模型名
            cls._model = SentenceTransformer(EMBEDDING_MODEL_PATH)
        return cls._instance

    def encode_query(self, text: str) -> list[float]:
        """生成用户查询的向量，必须加检索指令前缀"""
        full_text = self.QUERY_PREFIX + text
        vector = self._model.encode(full_text, normalize_embeddings=True)
        return vector.tolist()

    def encode_doc(self, text: str) -> list[float]:
        """生成单条知识库文档的向量，不加前缀"""
        vector = self._model.encode(text, normalize_embeddings=True)
        return vector.tolist()

    def encode_doc_batch(self, texts: list[str]) -> list[list[float]]:
        """批量生成文档向量，不加前缀"""
        vectors = self._model.encode(texts, normalize_embeddings=True, batch_size=32)
        return vectors.tolist()


# 全局单例
embedding_service = EmbeddingService()
