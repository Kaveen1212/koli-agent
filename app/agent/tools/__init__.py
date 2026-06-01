from app.agent.tools.artwork import search_art, get_item_details, filter_by_category, find_artwork_by_title
from app.agent.tools.room import analyze_room
from app.agent.tools.visualize import visualize_on_wall
from app.agent.tools.decor import stage_decor

TOOLS = [search_art, get_item_details, filter_by_category, find_artwork_by_title, analyze_room, visualize_on_wall, stage_decor]
