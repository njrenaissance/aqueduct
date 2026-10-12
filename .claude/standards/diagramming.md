# Diagramming

Diagrams are **infrastructure and system architecture documentation as code** — generated from Python using the [`diagrams` library](https://diagrams.mingrammer.com/), never hand-drawn. This keeps them:

- **Versionable** — live in git alongside code and evolve with architectural changes
- **Reviewable** — code review catches architectural decisions before they ship
- **Accurate** — regenerated from code, so they can't drift from reality
- **Focused** — one diagram per abstraction layer (infrastructure, orchestration, application), so each is readable in 30 seconds

A hand-drawn diagram is a debt that will be wrong by next quarter.

## When to Diagram

Use diagrams for:
- **System topology** — how services and infrastructure layers connect
- **Network boundaries** — VPCs, subnets, fault domains, security layers
- **Entry and exit points** — load balancers, gateways, external integrations
- **Data flow** — which systems talk to which, the critical path through the infrastructure

Do **not** diagram:
- **Implementation details** — how a service handles retries or internal state machines (use code comments instead)
- **Trivial systems** — a single microservice doesn't need a diagram
- **Frequently changing** — if you redraw it weekly, it's not a contract, it's a snapshot (use code comments instead)
- **Sequential logic** — use flowcharts (Mermaid) for request lifecycles and decision trees, not infrastructure diagrams

## One Layer Per Diagram

A diagram mixing infrastructure + orchestration + application becomes unreadable. Instead:

- **Infrastructure**: Network topology, load balancers, compute nodes, persistent storage
- **Orchestration**: Control planes, worker nodes, pod replicas (if not trivial)
- **Application**: Microservices, their dependencies, async job queues

Each is its own diagram. A reviewer should understand it without squinting or context-switching.

## Structure

Diagrams live outside `src/` under `docs/architecture/`. Each diagram has two files:

### Python Script: `docs/architecture/<name>.py`

```python
from diagrams import Diagram, Cluster, Edge
from diagrams.aws.network import ALB
from diagrams.aws.compute import EC2

diagram = Diagram(
    "System Name - Layer",
    filename="docs/architecture/output/<name>",
    show=False,
    direction="TB",
)

with diagram:
    # ... build structure with Clusters and Edges
```

Run it to generate `docs/architecture/output/<name>.png`:
```bash
python docs/architecture/<name>.py
```

### Markdown: `docs/architecture/<name>.md`

Documents the diagram's intent and scope:

```markdown
# System Name - Layer Name

Brief description of what this diagram shows.

![Diagram](path/to/diagram.png)

## Scope

**Layer**: Which abstraction level (infrastructure/orchestration/application).

**What it shows**: The critical path and key contracts.

## What Is NOT Shown

Explicitly list what's excluded (pod replicas, security groups, internal meshes, etc.).
The exclusions matter — they're why the diagram is readable.

## Related Diagrams

- [Other diagram](...) — context or related layer
```

Companion markdown explains why the diagram looks the way it does, so reviewers and future readers understand the intent.

## Commands

Generate or regenerate all diagrams:

```bash
mkdir -p docs/architecture/output
python docs/architecture/*.py
```

Commit the `.py` scripts and `.png` images to git — the `.png` is the rendered truth, and git history shows how the architecture evolved.

## Philosophy

See [the diagramming philosophy](https://github.com/njrenaissance/project-templates/blob/main/DIAGRAMMING_PHILOSOPHY.md) in the template repository for deeper context on why this approach, when to diagram, and common pitfalls.
