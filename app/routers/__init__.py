from app.routers.matches import router as matches_router
from app.routers.predictions import router as predictions_router
from app.routers.analytics import router as analytics_router

__all__ = ["matches_router", "predictions_router", "analytics_router"]
