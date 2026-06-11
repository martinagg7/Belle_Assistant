"""Belle's initial greeting at startup.

No questions are asked — the profile is configured by the family from the web
app (Belle Care).
"""

GREETING = (
    "¡Hola! Soy Bely, tu asistente de voz. "
    "Estoy aquí para acompañarte y ayudarte en tu día a día con cualquier cosa que necesites; "
    "solo tienes que decir mi nombre y estaré encantada de atenderte. "
    "¡Hasta pronto!"
)


def launch(tts) -> None:
    """Speak the welcome greeting once at startup."""
    print("[Onboarding] initial greeting")
    tts.speak_text(GREETING)
    print("[Onboarding] done")
