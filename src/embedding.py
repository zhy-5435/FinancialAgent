from sentence_transformers import SentenceTransformer
from src.config import EMBEDDING_MODEL_NAME, VECTOR_DIM

class EmbeddingService:
    _instance = None
    _model = None
    # BGE 中文检索固定指令前缀（仅查询用，文档不用加）
    QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._model = SentenceTransformer(EMBEDDING_MODEL_NAME)
        return cls._instance

    def encode_query(self, text: str) -> list[float]:
        """生成用户查询的向量，必须加前缀"""
        full_text = self.QUERY_PREFIX + text
        vector = self._model.encode(full_text, normalize_embeddings=True)
        return vector.tolist()

    def encode_doc(self, text: str) -> list[float]:
        """生成知识库文档的向量，不加前缀"""
        vector = self._model.encode(text, normalize_embeddings=True)
        return vector.tolist()

    def encode_doc_batch(self, texts: list[str]) -> list[list[float]]:
        """批量生成文档向量，不加前缀"""
        vectors = self._model.encode(texts, normalize_embeddings=True, batch_size=32)
        return vectors.tolist()

# 全局单例实例
embedding_service = EmbeddingService()
