from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from trailforge.models.audit import AuditLog
from trailforge.models.users import User

UTC = UTC


def test_health_reports_sqlite_configuration(client) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "sqlite"
    assert body["foreign_keys"] == 1
    assert body["journal_mode"] == "wal"


def test_validation_error_has_structured_response(client) -> None:
    response = client.post(
        "/api/v1/users",
        json={"email": "broken", "display_name": ""},
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "request_validation_error"
    assert detail["message"] == "request validation failed"
    assert len(detail["errors"]) >= 2


def test_api_complete_business_chain_and_database_side_effects(client) -> None:
    user_response = client.post(
        "/api/v1/users",
        json={
            "email": "api-hiker@example.com",
            "display_name": "API Hiker",
            "timezone": "Asia/Shanghai",
        },
    )
    assert user_response.status_code == 201
    user = user_response.json()
    user_id = user["id"]
    profile_response = client.put(
        f"/api/v1/users/{user_id}/sport-profile",
        params={"actor_id": user_id},
        json={
            "height_cm": 170,
            "weight_kg": 64,
            "fitness_level": "intermediate",
            "outdoor_experience": "Local hiking",
            "weekly_training_minutes": 180,
        },
    )
    assert profile_response.status_code == 200
    route_response = client.post(
        "/api/v1/routes",
        params={"actor_id": user_id},
        json={
            "name": "API Ridge",
            "region": "API Mountains",
            "description": "Stored locally",
            "distance_km": 8,
            "elevation_gain_m": 400,
            "elevation_loss_m": 400,
            "min_altitude_m": 100,
            "max_altitude_m": 500,
            "estimated_duration_minutes": 180,
            "difficulty": "moderate",
            "is_loop": True,
            "is_published": True,
            "segments": [
                {
                    "sequence": 1,
                    "name": "Loop",
                    "distance_km": 8,
                    "elevation_gain_m": 400,
                    "estimated_duration_minutes": 180,
                    "difficulty": "moderate",
                    "start_latitude": 30,
                    "start_longitude": 120,
                    "end_latitude": 30,
                    "end_longitude": 120,
                }
            ],
            "points": [],
            "risk_tag_ids": [],
        },
    )
    assert route_response.status_code == 201, route_response.text
    route = route_response.json()
    start = datetime.now(UTC) + timedelta(days=10)
    expedition_response = client.post(
        "/api/v1/expeditions",
        json={
            "organizer_id": user_id,
            "route_id": route["id"],
            "name": "API Expedition",
            "meeting_location": "Trailhead",
            "meeting_at": (start - timedelta(hours=1)).isoformat(),
            "start_at": start.isoformat(),
            "end_at": (start + timedelta(hours=6)).isoformat(),
            "registration_deadline": (start - timedelta(days=1)).isoformat(),
            "capacity": 5,
            "minimum_fitness_level": 1,
            "risk_level": "moderate",
        },
    )
    assert expedition_response.status_code == 201, expedition_response.text
    expedition = expedition_response.json()
    open_response = client.post(
        f"/api/v1/expeditions/{expedition['id']}/status",
        json={"target_status": "open", "actor_id": user_id, "reason": "Ready"},
    )
    assert open_response.status_code == 200
    roster_response = client.get(f"/api/v1/expeditions/{expedition['id']}/roster")
    assert roster_response.status_code == 200
    roster = roster_response.json()
    assert roster["confirmed_count"] == 1
    assert roster["members"][0]["role"] == "leader"
    dashboard = client.get("/api/v1/statistics/dashboard")
    assert dashboard.status_code == 200
    assert dashboard.json()["active_users"] == 1
    assert dashboard.json()["published_routes"] == 1
    database = client.app.state.database
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(User)) == 1
        assert session.scalar(select(func.count()).select_from(AuditLog)) >= 5


def test_api_duplicate_email_returns_conflict_and_rolls_back(client) -> None:
    payload = {"email": "duplicate@example.com", "display_name": "First"}
    assert client.post("/api/v1/users", json=payload).status_code == 201
    response = client.post(
        "/api/v1/users",
        json={"email": "DUPLICATE@example.com", "display_name": "Second"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "conflict"
    with client.app.state.database.session() as session:
        users = session.query(User).filter_by(email="duplicate@example.com").all()
        assert len(users) == 1
        assert users[0].display_name == "First"


def test_api_route_pagination_and_sorting(client) -> None:
    user = client.post(
        "/api/v1/users",
        json={"email": "sorter@example.com", "display_name": "Sorter"},
    ).json()
    for index, distance in enumerate((4, 7, 10)):
        response = client.post(
            "/api/v1/routes",
            params={"actor_id": user["id"]},
            json={
                "name": f"Route {index}",
                "region": "Sorted",
                "distance_km": distance,
                "elevation_gain_m": 100,
                "estimated_duration_minutes": 120,
                "difficulty": "easy",
                "segments": [
                    {
                        "sequence": 1,
                        "name": "Segment",
                        "distance_km": distance,
                        "estimated_duration_minutes": 120,
                        "difficulty": "easy",
                        "start_latitude": 0,
                        "start_longitude": 0,
                        "end_latitude": 0.1,
                        "end_longitude": 0.1,
                    }
                ],
            },
        )
        assert response.status_code == 201, response.text
    response = client.get(
        "/api/v1/routes",
        params={
            "region": "Sorted",
            "page": 1,
            "page_size": 2,
            "sort": "distance_km",
            "direction": "desc",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["meta"] == {"page": 1, "page_size": 2, "total": 3, "pages": 2}
    assert [item["distance_km"] for item in body["items"]] == [10, 7]


def test_api_unknown_sort_field_is_clear_422(client) -> None:
    response = client.get("/api/v1/routes", params={"sort": "drop_table"})
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "domain_validation_error"
    assert "allowed" in detail["context"]
