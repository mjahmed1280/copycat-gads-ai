import pandas as pd
import sys

input_file = sys.argv[1] if len(sys.argv) > 1 else "RSA AI Generator - Template.csv"
output_file = input_file.replace(".csv", "_converted.csv")

df = pd.read_csv(input_file)

df = df.rename(columns={"Campaign Id": "Campaign ID", "Adgroup Name": "Ad Group"})

if "Keywords" in df.columns:
    df["Keywords"] = df["Keywords"].str.replace("\n", ", ", regex=False)

if "Headline 1" in df.columns:
    h = df["Headline 1"].str.split("\n", expand=True)
    for i, col in enumerate(h.columns):
        df[f"Headline {i + 1}"] = h[col].str.strip()

if "Description 1" in df.columns:
    d = df["Description 1"].str.split("\n", expand=True)
    for i, col in enumerate(d.columns):
        df[f"Description {i + 1}"] = d[col].str.strip()

df.to_csv(output_file, index=False)
print(f"Saved: {output_file} ({len(df)} rows)")
