from dependency_injector import containers, providers

from app.infra.chroma import ChromaClient
from app.infra.doc_store import DocumentStore
from app.infra.embedding import EmbeddingService
from app.infra.llm import LLMService
from app.infra.bm25 import BM25Searcher
from app.infra.cache import CacheService

from app.api.document.repository import DocumentRepository
from app.api.document.service import DocumentServiceImpl
from app.api.query.repository import QueryRepository
from app.api.query.service import QueryServiceImpl

class Container(containers.DeclarativeContainer):
    wiring_config = containers.WiringConfiguration(
        modules=["app.api.document.router", "app.api.query.router"]
    )

    config = providers.Configuration()

    # 인프라 (Singleton) : 앱 전체에서 인스턴스 1개
    chroma_client = providers.Singleton(
        ChromaClient,
        persist_dir=config.chroma_persist_dir,
    )
    doc_store = providers.Singleton(
        DocumentStore,
        path=config.doc_store_path,
    )
    embedding_service = providers.Singleton(
        EmbeddingService,
        model_name=config.embedding_model,
    )

    # BM25 검색 (Singleton, 문서 변경 시 lazy rebuild)
    bm25_searcher = providers.Singleton(
        BM25Searcher,
        chroma=chroma_client,
    )

    # LLM 호출 (Singleton, claude code sdk)
    llm_service = providers.Singleton(
        LLMService
    )

    # 캐시 (Singleton, Layer 1 + Layer 2)
    cache_service = providers.Singleton(
        CacheService,
        redis_url=config.redis_url,
    )

    # Repository
    document_repository = providers.Factory(
        DocumentRepository,
        chroma=chroma_client,
        doc_store=doc_store,
    )

    # Service
    document_service = providers.Factory(
        DocumentServiceImpl,
        repository=document_repository,
        embedding=embedding_service,
        bm25=bm25_searcher,
        cache=cache_service,
    )

    # Query
    query_repository = providers.Factory(
        QueryRepository,
        chroma=chroma_client,
        bm25=bm25_searcher,
        embedding=embedding_service,
        doc_store=doc_store,
    )

    query_service = providers.Factory(
        QueryServiceImpl,
        repository=query_repository,
        llm=llm_service,
        cache=cache_service,
    )
