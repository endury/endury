# endury/wiring

Composition: the configuration and the repository container. The browser
builds `CoachConfig` from the settings panel; native tools and tests read
it from `.env` through `get_coach_config()`.

| Module | What it does |
| --- | --- |
| `config.py` | `CoachConfig`: keys, model, transport knobs, coach title, welcome openers; `get_coach_config()` and the placeholder-key guard. |
| `container.py` | `RepositoryContainer`: one `DatabaseSession` and every repository, in memory or on disk; `bind_intervals_client`. |

Start reading: [config.py](config.py).
