from obsidian_mcp import frontmatter_template, template_bodies


def test_filenames_cover_exactly_the_eleven_types():
    assert set(template_bodies.FILENAMES) == set(frontmatter_template.TYPE_FOLDER)


def test_bodies_cover_exactly_the_eleven_types():
    assert set(template_bodies.BODIES) == set(frontmatter_template.TYPE_FOLDER)


def test_filenames_are_unique():
    assert len(set(template_bodies.FILENAMES.values())) == len(template_bodies.FILENAMES)
