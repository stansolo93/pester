"""pester demo — create a pre-populated demo vault to try pester instantly."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import click


_DEMO_CONFIG = """\
vault:
  name: Demo Vault
  language: en
  owner: founder

extraction:
  keywords:
    en: [TODO, action item, deadline, assigned to, by end of]

health:
  journal_stale_days: 3
  decision_review_days: 30
"""

_MEETING_RECENT = """\
# Product Sync — {date}

**Attendees:** founder, alex, maria

## Decisions
- Ship v2 of the matching engine by end of Q2
- Hire a senior backend engineer by next month

## Action Items
- TODO @founder — finalize pricing model — by {due_soon}
- TODO @alex — write API documentation — by {due_week}
- TODO @maria — set up staging environment — by {due_week}
"""

_MEETING_OLD = """\
# Investor Update — {date}

**Attendees:** founder, investors

## Key Metrics
- MRR: $12K (up 40% MoM)
- Active users: 340
- Churn: 2.1%

## Action Items
- TODO @founder — send updated cap table — by {overdue_date}
- TODO @founder — prepare board deck for Q2 — by {due_soon}
"""

_JOURNAL = """\
# {date}

Good energy today. Closed 3 items from the backlog.
Need to follow up with Alex on the API docs — it's been a week.

Key insight: our onboarding flow drops 40% at step 3. Worth investigating.
"""

_GOAL = """\
---
status: active
target_date: {target}
tags: [product, launch]
---
# Ship MVP to first 10 customers

Success criteria:
- Core features working end-to-end
- 10 paying customers onboarded
- NPS > 40
"""

_DECISION = """\
---
status: active
review_date: {review}
---
# Use PostgreSQL for primary datastore

## Context
Evaluated PostgreSQL, MySQL, and DynamoDB for our main database.

## Decision
PostgreSQL. Better JSON support, extensions ecosystem, and team familiarity.

## Consequences
- Need to set up connection pooling (PgBouncer)
- Migration path from SQLite prototype is straightforward
"""

_PERSON_FOUNDER = """\
---
role: Founder
---
# Founder

Responsible for product vision, fundraising, and team building.
"""

_PERSON_ALEX = """\
---
role: Engineer
---
# Alex

Senior engineer. Owns the API layer and developer experience.
"""


@click.command()
@click.argument("path", default="demo-vault", required=False)
def demo(path: str) -> None:
    """Create a pre-populated demo vault to try pester instantly.

    Creates a vault with sample meetings, actions, a journal entry,
    a goal, a decision record, and team profiles. Then shows vault
    health and action status.
    """
    target = Path(path).resolve()
    if target.exists():
        raise click.ClickException(f"Directory already exists: {target}")

    today = date.today()
    yesterday = today - timedelta(days=1)
    due_soon = (today + timedelta(days=3)).isoformat()
    due_week = (today + timedelta(days=7)).isoformat()
    overdue_date = (today - timedelta(days=5)).isoformat()
    target_q2 = (today + timedelta(days=60)).isoformat()
    review_date = (today + timedelta(days=30)).isoformat()

    # Create directory structure
    for d in [
        "meetings",
        "journal",
        "actions",
        "goals",
        "decisions",
        "people",
        "projects",
        "reference",
    ]:
        (target / d).mkdir(parents=True, exist_ok=True)

    # Config
    (target / "pester.yaml").write_text(_DEMO_CONFIG)

    # Meetings
    (target / "meetings" / f"{today.isoformat()}-product-sync.md").write_text(
        _MEETING_RECENT.format(date=today.isoformat(), due_soon=due_soon, due_week=due_week)
    )
    (
        target / "meetings" / f"{(today - timedelta(days=10)).isoformat()}-investor-update.md"
    ).write_text(
        _MEETING_OLD.format(
            date=(today - timedelta(days=10)).isoformat(),
            overdue_date=overdue_date,
            due_soon=due_soon,
        )
    )

    # Journal
    (target / "journal" / f"{yesterday.isoformat()}.md").write_text(
        _JOURNAL.format(date=yesterday.isoformat())
    )

    # Goals
    (target / "goals" / "ship-mvp.md").write_text(_GOAL.format(target=target_q2))

    # Decisions
    (target / "decisions" / "use-postgresql.md").write_text(_DECISION.format(review=review_date))

    # People
    (target / "people" / "founder.md").write_text(_PERSON_FOUNDER)
    (target / "people" / "alex.md").write_text(_PERSON_ALEX)

    # Pre-created actions (so health and standup have data)
    from pester.tracking.actions import create_action

    create_action(
        target,
        description="Send updated cap table",
        owner="founder",
        due=overdue_date,
        source="meeting",
    )
    create_action(
        target,
        description="Finalize pricing model",
        owner="founder",
        due=due_soon,
        source="meeting",
        priority="Must",
    )
    create_action(
        target, description="Write API documentation", owner="alex", due=due_week, source="meeting"
    )

    click.echo(f"\n✨ Demo vault created at {target}\n")
    click.echo("Try these commands:\n")
    click.echo(f"  cd {path}")
    click.echo("  pester health          # vault health report")
    click.echo("  pester actions         # list action items")
    click.echo("  pester standup         # daily standup notes")
    click.echo("  pester status          # one-line summary")
    click.echo("  pester actions --json  # machine-readable output")
    click.echo()
