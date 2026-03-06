import re

input_path = "config/city_merge.json"
output_path = "config/city_merge.yaml"

with open(input_path, encoding="utf-8") as fin, open(output_path, "w", encoding="utf-8") as fout:
    for line in fin:
        line = line.rstrip("\n")
        # Replace // style comments with #
        line = re.sub(r'\/\/', '#', line)
        # Remove opening/closing curly braces
        if line.strip() == '{' or line.strip() == '}':
            continue
        # Remove any trailing commas
        line = re.sub(r',(\s*#)', r'\1', line)
        fout.write(line + "\n")
