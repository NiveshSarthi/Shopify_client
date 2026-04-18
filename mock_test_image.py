import json
import os
from pillowtest import generate_pillow_image

# Load the sample order data from your u.json file
with open("../u.json", "r") as f:
    order_data = json.load(f)

print("🎨 Generating Mock Image using actual order data...")

# Generate the image
# This will use the logic in pillowtest.py but with the data from your json
output_path = generate_pillow_image(order_data)

print(f"✅ Success! You can now view the image here: {output_path}")
