"""Comic script + panel-art prompt wording (continuity over room notes; no bubbles in art)."""
from server.render.flux_generator import nearest_aspect_ratio, panel_art_prompt, remote_image_size_fields
from server.story.narrative_engine import comic_script_system, comic_script_user


def test_panel_art_prompt_forbids_speech_bubbles():
    prompt = panel_art_prompt("a detective stares at a glowing turnip")
    assert prompt.startswith("comic panel: a detective stares at a glowing turnip")
    assert "no speech bubbles" in prompt
    assert "no dialogue balloons" in prompt
    assert "no text" in prompt
    assert "top third" in prompt  # default text_anchor clears the top


def test_panel_art_prompt_appends_art_style():
    prompt = panel_art_prompt("a detective stares at a glowing turnip", "high-contrast ink")
    assert "Art style: high-contrast ink." in prompt
    assert panel_art_prompt("scene", "") == panel_art_prompt("scene")


def test_panel_art_prompt_clears_space_for_text_anchor():
    prompt = panel_art_prompt("a chase through neon alleys", text_anchor="bottom-left")
    assert "lower-left" in prompt
    assert "faces and key subjects toward the right" in prompt


def test_flux3_remote_size_fields_use_tier_and_aspect():
    assert remote_image_size_fields("black-forest-labs/flux-3-image", (380, 420)) == {
        "resolution": "768",
        "aspect_ratio": nearest_aspect_ratio(380, 420),
    }
    assert remote_image_size_fields("", (380, 420)) == {"size": "380x420"}
    assert nearest_aspect_ratio(800, 480) == "16:9"  # 1.667 closest to 16:9 among supported


def test_comic_script_prompts_prioritize_continuity_over_room_notes():
    system = comic_script_system("Pond Ops", "cozy mystery", "dry wit", "ink wash")
    assert "continuity is primary" in system.lower() or "Story continuity is primary" in system
    assert "at most one" in system.lower()
    assert "visuals only" in system.lower() or "visuals only" in system
    assert "ink wash" in system
    assert "enforced by the server" in system

    user = comic_script_user(
        title="Pond Ops",
        genre="cozy mystery",
        premise="Snails solve crimes.",
        art_style="ink wash",
        cast="\nCast:\n- Pip: a wary snail",
        arc_progress=0.4,
        panels_used=8,
        target_panels=20,
        series_issue=3,
        history_section="Story So Far …\n",
        mined_elements=["[TENSE • HIGH] the kettle screamed about taxes"],
    )
    assert "Room notes" in user
    assert "single most compelling" in user
    assert "this arc only" in user.lower()
    assert "closed series" in user.lower()
    assert "Art style (required in every scene_description): ink wash" in user
    assert "exact number of room notes the server will feed" in user
    # history_section carries the "Story So Far for this arc only" banner when present
    with_history = comic_script_user(
        title="Pond Ops", genre="cozy mystery", premise="x", art_style="ink wash", cast="",
        arc_progress=0.4, panels_used=8, target_panels=20, series_issue=3,
        history_section=(
            "Story So Far for this arc only ('Pond Ops') — earlier series are closed and "
            "must not appear here or be continued. …\n\n"
        ),
        mined_elements=[],
    )
    assert "Story So Far for this arc only" in with_history
    assert "earlier series" in with_history.lower()
    assert "No speech bubbles" in user or "no speech bubbles" in user.lower()
    assert "at most one" in user.lower() and "room note" in user.lower()
    assert "Match layout_type to this issue's mood" in user
    assert "CRITICAL STRIKE" in user
    assert "diagonal" in user.lower() and "kinetic" in user.lower()
    assert "vary layouts" in system.lower() or "do not default to the same" in system.lower()
    assert "text_anchor" in user
    assert "negative space" in user.lower() or "clear empty" in user.lower()
    assert "six-grid" in user  # expanded layout menu is offered to the model
