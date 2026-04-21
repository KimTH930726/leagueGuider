class AppError(Exception):
    """기본 앱 예외"""
    pass

class ConfigError(AppError):
    pass

class ConfluenceError(AppError):
    pass

class SyncError(AppError):
    pass

class SearchError(AppError):
    pass

class ReportError(AppError):
    pass

class EmbeddingError(AppError):
    pass

class VectorStoreError(AppError):
    pass
