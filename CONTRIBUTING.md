# Contributing to Zendure Home Assistant Integration

Thank you for your interest in contributing to the Zendure HA integration! Whether you're reporting a bug, requesting a feature, or submitting code, your contribution is valued.

## Table of Contents

- [Reporting Bugs](#reporting-bugs)
- [Requesting Features](#requesting-features)
- [Contributing Code](#contributing-code)
- [Development Setup](#development-setup)
- [Code of Conduct](#code-of-conduct)

---

## Reporting Bugs

Found a bug? We'd love to hear about it! Here's how to report it effectively:

### Before You Report

- **Check existing issues**: Search [GitHub Issues](https://github.com/zendure/zendure-ha/issues) to see if your bug has already been reported.
- **Check the log**: Enable debug logging in Home Assistant to capture the error. This helps us track down the root cause faster.

### How to Report

1. Go to [GitHub Issues](https://github.com/zendure/zendure-ha/issues)
2. Click **New Issue** and select **Bug Report**
3. Fill in the required fields:
   - **Home Assistant version** (e.g., 2025.4.0)
   - **Zendure integration version** (find in Settings → Devices & Services → Zendure HA)
   - **Device type** (e.g., SolarFlow 2400 AC)
   - **Description** of what went wrong
   - **Steps to reproduce** the issue
   - **Expected behavior** (what should happen)
   - **Log extracts** (from `config/home-assistant.log` or HA's web UI logs)

### What to Include in Logs

Enable debug logging by adding this to `configuration.yaml`:

```yaml
logger:
  default: info
  logs:
    custom_components.zendure_ha: debug
```

Then restart Home Assistant and reproduce the issue. Attach the relevant lines from the log.

---

## Requesting Features

Have an idea for improvement? We're open to suggestions!

### Before You Request

- **Check existing requests**: Look at [GitHub Issues](https://github.com/zendure/zendure-ha/issues?q=label%3Afeature-request) to avoid duplicates.
- **Is it in scope?** This integration focuses on power management and monitoring for Zendure devices. Hardware-specific issues should be reported to Zendure.

### How to Request

1. Go to [GitHub Issues](https://github.com/zendure/zendure-ha/issues)
2. Click **New Issue** and select **Feature Request**
3. Clearly describe:
   - What you want to do
   - Why it would be useful
   - Which device(s) it affects

**Note**: Device support requests for newer models are welcome, but we need accurate API documentation from Zendure to implement them.

---

## Contributing Code

### Getting Started

1. **Fork** the repository on GitHub
2. **Clone** your fork: `git clone https://github.com/YOUR_USERNAME/zendure-ha.git`
3. **Create a branch** with a descriptive name:
   - Bug fixes: `fix/short-description` (e.g., `fix/passthrough-classification`)
   - Features: `feature/short-description` (e.g., `feature/add-superbase-support`)
   - Refactoring: `refactor/short-description`
4. **Make your changes** and commit with clear messages (see below)
5. **Push** to your fork and **create a Pull Request** against `master`

### Commit Messages

Keep commits focused and descriptive:

```
Short description (50 chars max)

Longer explanation if needed, wrapping at 72 characters.
Explain the why, not just the what.

Fixes #123 (if applicable)
```

### Code Style

- Follow existing code patterns in the project
- Use type hints where possible
- Include docstrings for new functions
- Keep functions focused and readable

### Testing Your Changes

Before submitting a PR, test locally:

```bash
scripts/develop
```

This starts a Home Assistant instance in a dev container where you can verify your changes.

### What We Look For

- ✅ Code that solves the problem cleanly
- ✅ Respect for existing architecture (see [`docs/architecture.md`](docs/architecture.md))
- ✅ Tests or manual verification steps documented
- ✅ Clear commit history (no "fix typo" commits for small changes — squash them)
- ✅ No breaking changes to existing config or entities (unless discussed in the issue first)

---

## Development Setup

For a complete guide to setting up your development environment and adding new devices, see [`docs/development.md`](docs/development.md).

Quick start:

```bash
# Clone and enter the repo
git clone https://github.com/zendure/zendure-ha.git
cd zendure-ha

# Install dev dependencies
scripts/setup

# Start Home Assistant with the integration mounted
scripts/develop
```

Then:
- Open Home Assistant at http://localhost:8123
- Add the Zendure integration from Settings → Devices & Services
- Check the logs with `F12` → Logs tab

---

## Code of Conduct

### Our Pledge

We are committed to providing a welcoming and inclusive environment for all contributors, regardless of experience level, background, or identity.

### Expected Behavior

- Be respectful and inclusive in all interactions
- Provide and accept constructive feedback gracefully
- Focus on what is best for the community
- Show empathy towards others

### Unacceptable Behavior

- Harassment, discrimination, or derogatory language
- Attacks on someone's identity
- Unwelcome sexual attention or advances
- Trolling or deliberate incitement

### Reporting Issues

If you experience or witness unacceptable behavior, please report it privately to @fireson on GitHub or via the issue tracker. All reports will be taken seriously and investigated fairly.

---

## Questions?

- **Integration docs**: Read [`docs/how-it-works.md`](docs/how-it-works.md) for usage or [`docs/architecture.md`](docs/architecture.md) for technical details
- **Discussions**: Start a [GitHub Discussion](https://github.com/zendure/zendure-ha/discussions) for questions
- **Issues**: Use [GitHub Issues](https://github.com/zendure/zendure-ha/issues) for bugs and features

---

Thank you for being part of the Zendure HA community! 🙏
