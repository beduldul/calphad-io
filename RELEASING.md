# Releasing `calphad-io`

This project publishes to PyPI with **Trusted Publishing (OIDC)**. There is no
API token to create, store, or rotate — GitHub mints a short-lived OIDC token at
publish time and PyPI trusts it.

Two parts: a one-time setup you do once, and a per-release flow you repeat.

---

## Part 1 — One-time setup (owner, do this once)

You must do these; an automated agent cannot create accounts or click PyPI UI.

1. **Create a PyPI account** if you do not have one: <https://pypi.org/account/register/>
2. **Enable 2FA** on that account. PyPI requires 2FA to publish.
3. **Add a *pending* publisher.** `calphad-io` does not exist on PyPI yet, so use
   the *pending* publisher form, not the "existing project" form:
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

Once the first release is live, the README install line can be simplified from:

```bash
pip install git+https://github.com/beduldul/calphad-io.git
```

to:

```bash
pip install calphad-io
```

This has **deliberately not been changed yet**: `calphad-io` is not on PyPI
(<https://pypi.org/pypi/calphad-io/json> returns HTTP 404), so the current
`git+https://` line is the only one that works today. Change it in the same
commit that follows the first successful publish.

---

## Verifying a release

```bash
pip install calphad-io==<version>
python -c "import calphad_io; print(calphad_io.__version__)"
calphad-io info tests/data/alfe_sei.TDB
```
