# Repository Guidelines

## Project Structure & Module Organization
This is a Python-based LLM integration project with the following structure:
- `main.py`: Entry point for the application
- `src/`: Core source code, organized by feature module
- `tests/`: Unit and integration test suite
- `config/`: YAML/JSON configuration files
- `data/`: Static and generated project data
- `docs/`: Project documentation and reference material

## Build, Test, and Development Commands
| Command | Purpose |
|---------|---------|
| `pip install -r requirements.txt` | Install all project dependencies |
| `python main.py` | Run the application locally |
| `pytest` | Execute the full test suite |
| `pytest --cov=src --cov-report=term` | Run tests with coverage reporting |

## Coding Style & Naming Conventions
- Follow PEP 8 standards for all Python code
- Use 4-space indentation (no tabs)
- Use snake_case for functions, variables, and file names
- Use PascalCase for class definitions
- Line length limit: 120 characters

## Testing Guidelines
- Use `pytest` as the testing framework
- Aim for minimum 80% test coverage for all new feature code
- Name test files with the pattern `test_<module>.py`
- Name test functions with the pattern `test_<behavior>_<scenario>`

## Commit & Pull Request Guidelines
- Use Conventional Commits format for all commit messages: `<type>(<scope>): <description>` (e.g. `feat(chat): add streaming response support`, `fix(config): resolve YAML parsing error`)
- Pull requests must include a clear description of changes, linked issues, and test results
- All CI checks must pass before merging
