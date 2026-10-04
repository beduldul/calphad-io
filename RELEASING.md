# Releasing `calphad-io`

This project publishes to PyPI with **Trusted Publishing (OIDC)**. There is no
API token to create, store, or rotate — GitHub mints a short-lived OIDC token at
publish time and PyPI trusts it.

Two parts: a one-time setup you do once, and a per-release flow you repeat.

---

## Part 1 — One-time setup (owner, done)

> **Done — `calphad-io` 0.1.1 is published.** The pending publisher was
> converted to a normal publisher on the project, so this section is kept only
> as a record of how the first release was set up. Skip it for future releases.

1. **Create a PyPI account** if you do not have one: <https://pypi.org/account/register/>
2. **Enable 2FA** on that account. PyPI requires 2FA to publish.
3. **Add a *pending* publisher** (this is what the first release used):
   <https://pypi.org/manage/account/publishing/>

   Enter **exactly** these values (they must match the workflow byte-for-byte):

   | Field | Value |
   |---|---|
   | PyPI Project Name | `calphad-io` |
   | Owner | `beduldul` |
   | Repository name | `calphad-io` |
   | Workflow name | `release.yml` |
   | Environment name | `pypi` |

4. **Do not create an API token.** None is needed.

> **First-release caveat.** A brand-new project needs a *pending* publisher
> (step 3). The **very first upload must come from this workflow** — you cannot
> create the project by hand on PyPI and then attach the publisher, because the
> name is claimed by the first upload. After the first successful publish, the
> pending publisher becomes a normal publisher on the project and every later tag
> just works.

---

## Part 2 — Per-release flow

```bash
# 1. Bump the version in pyproject.toml (and src/calphad_io/__init__.py __version__).
# 2. Add a CHANGELOG.md entry under a new "## [x.y.z] - YYYY-MM-DD" heading.
# 3. Commit.
git commit -am "release: v0.2.0"

# 4. Tag and push the tag. This is what triggers the workflow.
git tag v0.2.0
git push origin v0.2.0
```

Pushing a tag matching `v*` triggers `.github/workflows/release.yml`, which:

1. builds the sdist and wheel with `python -m build`,
2. runs `twine check` on both (a malformed artifact cannot ship),
3. publishes to PyPI via `pypa/gh-action-pypi-publish` using OIDC.

Watch it under the **Actions** tab. The publish job runs in the `pypi`
environment, so if you later add required reviewers there, the publish will wait
for approval.

---

## README install line

The first release is live, so the README install line is the plain PyPI
command:

```bash
pip install calphad-io
```

A GitHub-install alternative is kept only for tracking `main` ahead of a
release, and is labelled as such. If a future release is ever yanked, revisit
this line rather than assuming the PyPI install still works.

---

## Verifying a release

```bash
pip install calphad-io==<version>
python -c "import calphad_io; print(calphad_io.__version__)"
calphad-io info tests/data/alfe_sei.TDB
```
