from fastapi import APIRouter

from trailforge.api import activities, gear, routes, safety, statistics, training, users

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(users.router)
api_router.include_router(training.router)
api_router.include_router(routes.router)
api_router.include_router(activities.router)
api_router.include_router(gear.router)
api_router.include_router(safety.router)
api_router.include_router(statistics.router)
