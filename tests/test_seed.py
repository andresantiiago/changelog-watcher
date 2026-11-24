def test_seed_overwrite_keeps_undeclared_webhook_environments(tmp_path, client, seeded):
    from changelog_watcher.db import session_scope
    from changelog_watcher.seed import load_seed

    hook = client.post("/api/webhooks", json={"name": "hook", "url": "https://h/x",
                                              "environment_ids": [seeded["prod"]["id"]]}).json()
    cfg = tmp_path / "c.yaml"
    cfg.write_text("webhooks:\n  - name: hook\n    url: https://h/y\n    kind: slack\n")
    with session_scope() as s:
        load_seed(s, str(cfg), overwrite=True)
    with session_scope() as s:
        from changelog_watcher.models import Webhook

        w = s.get(Webhook, hook["id"])
        assert (w.kind, w.url, w.environment_ids) == ("slack", "https://h/y", [seeded["prod"]["id"]])
    cfg.write_text("webhooks:\n  - name: hook\n    url: https://h/y\n    environments: [desenvolvimento]\n")
    with session_scope() as s:
        load_seed(s, str(cfg), overwrite=True)
    with session_scope() as s:
        assert s.get(Webhook, hook["id"]).environment_ids == [seeded["dev"]["id"]]
