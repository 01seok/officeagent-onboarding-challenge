from dependency_injector import containers, providers

from app.infra.chroma import ChromaClient
from app.infra.doc_store import DocumentStore
from app.infra.embedding import EmbeddingService
from app.api.document.repository import DocumentRepository
from app.api.document.service import DocumentServiceImpl


class Container(containers.DeclarativeContainer):
    wiring_config = containers.WiringConfiguration(
        modules=["app.api.document.router"]
    )

    config = providers.Configuration()

    # 인프라 (Singleton) — 앱 전체에서 인스턴스 1개
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
    )
