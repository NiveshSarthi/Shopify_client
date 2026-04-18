import json
import os
from pillowtest import generate_pillow_image

# Load the sample order data
with open("../u.json", "r") as f:
    order_data = json.load(f)

print("🧪 Generating Variant Preview...")

# Force a variant on the first item to show how it looks
if order_data.get("line_items"):
    order_data["line_items"][0]["variant_title"] = "Size: L / Color: Midnight Blue"

# Generate the image
output_path = generate_pillow_image(order_data)

print(f"✅ Variation Test Success! View here: {output_path}")
