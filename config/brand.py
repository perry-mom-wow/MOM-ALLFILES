"""mom-wow brand colour palette."""

# Primary
GREEN       = "#6B7C52"   # Olive green
GREEN_DARK  = "#3D4F30"   # Deep green
WHITE       = "#FFFFFF"
BLACK       = "#1A1A1A"
CREAM       = "#F0EDE6"   # Off-white / secondary background

# Accent colours
PINK        = "#E8929A"
TERRACOTTA  = "#A65D52"
ORANGE      = "#D4724A"
MUSTARD     = "#D4A83A"
BLUE_LIGHT  = "#A8D4D8"

# Semantic aliases used in charts / UI
PRIMARY     = GREEN
SECONDARY   = GREEN_DARK
DANGER      = TERRACOTTA
WARNING     = ORANGE
SUCCESS     = GREEN
NEUTRAL     = CREAM

# Chart colour sequence (green first, then accents)
CHART_SEQUENCE = [GREEN, GREEN_DARK, MUSTARD, ORANGE, TERRACOTTA, PINK, BLUE_LIGHT]

# Funnel stage colours (top → bottom of funnel)
FUNNEL_COLOURS = [GREEN_DARK, GREEN, MUSTARD, ORANGE, TERRACOTTA, PINK, BLUE_LIGHT]
