from pathlib import Path

from jinja2 import Environment, FileSystemLoader


def test_all_page_templates_compile():
    templates = Path(__file__).resolve().parents[1] / 'app' / 'templates'
    environment = Environment(loader=FileSystemLoader(templates))
    for template in templates.glob('*.html'):
        environment.get_template(template.name)
