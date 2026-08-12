from automarkov.provenance import _target_installation_package_names


def test_target_closure_revisits_base_dependencies_for_late_extras() -> None:
    lock = {
        "package": [
            {
                "name": "profile-root",
                "source": {"virtual": "."},
                "dependencies": [
                    {"name": "alpha-requester"},
                    {"name": "beta-requester"},
                    {"name": "shared"},
                ],
            },
            {
                "name": "alpha-requester",
                "dependencies": [{"name": "shared", "extra": ["alpha"]}],
            },
            {
                "name": "beta-requester",
                "dependencies": [{"name": "shared", "extra": ["beta"]}],
            },
            {
                "name": "shared",
                "dependencies": [
                    {"name": "always"},
                    {"name": "alpha-dependency", "marker": "extra == 'alpha'"},
                    {"name": "beta-dependency", "marker": "extra == 'beta'"},
                    {"name": "unrequested-dependency", "marker": "extra == 'gamma'"},
                    {"name": "wrong-platform", "marker": "sys_platform == 'win32'"},
                ],
                "optional-dependencies": {"alpha": [], "beta": []},
            },
            {"name": "always"},
            {"name": "alpha-dependency"},
            {"name": "beta-dependency"},
            {"name": "unrequested-dependency"},
            {"name": "wrong-platform"},
        ]
    }

    package_names = _target_installation_package_names(
        lock,
        python_version="3.11.13",
        target_platform="linux/amd64",
    )

    assert package_names == frozenset(
        {
            "profile-root",
            "alpha-requester",
            "beta-requester",
            "shared",
            "always",
            "alpha-dependency",
            "beta-dependency",
        }
    )
