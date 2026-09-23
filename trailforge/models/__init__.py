from __future__ import annotations


def load_all_models() -> None:
    from trailforge.models import activities as activities
    from trailforge.models import audit as audit
    from trailforge.models import gear as gear
    from trailforge.models import routes as routes
    from trailforge.models import safety as safety
    from trailforge.models import training as training
    from trailforge.models import users as users


__all__ = ["load_all_models"]
