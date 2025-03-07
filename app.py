import os
import json
import io
import base64
import pandas as pd
import gspread
import matplotlib
matplotlib.use('Agg')  # ✅ Fix: Use a non-GUI backend
import matplotlib.pyplot as plt

from flask import Flask, render_template, request, redirect, jsonify
from oauth2client.service_account import ServiceAccountCredentials
app = Flask(__name__)

# Ensure options.json exists
OPTIONS_FILE = "options.json"
if not os.path.exists(OPTIONS_FILE):
    with open(OPTIONS_FILE, "w") as f:
        json.dump({}, f, indent=4)

# Load stored dropdown values from options.json
with open(OPTIONS_FILE, "r") as f:
    stored_options = json.load(f)

# Load Google Sheets credentials
with open("config.json") as config_file:
    config = json.load(config_file)

SPREADSHEET_ID = config["SPREADSHEET_ID"]

# Google Sheets API Setup
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
client = gspread.authorize(creds)
sheet = client.open_by_key(SPREADSHEET_ID).sheet1  # Open the first sheet

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        form_data = request.form.to_dict(flat=False)
        columns = sheet.row_values(1)
        data = [", ".join(form_data.get(col, [""])) for col in columns]
        sheet.append_row(data)
        return redirect("/")

    # Read existing data from Google Sheets
    data = sheet.get_all_records()
    columns = sheet.row_values(1)

    # Ensure dropdowns load from BOTH Google Sheets and options.json
    df = pd.DataFrame(data)
    dropdown_options = {}

    dropdown_fields = [
        "Coach Code", "Schedule", "Division", "Secondary Suspension Type",
        "Type of Spring", "Colour of Spring", "Type of Failure", "Location", "Reason for Failure", "Remarks"
    ]

    for field in dropdown_fields:
        values_from_sheet = df[field].dropna().astype(str).unique().tolist() if field in df.columns else []
        stored_values = stored_options.get(field, [])  # Get stored values from options.json

        # Load options.json first, then merge with Google Sheets values
        dropdown_options[field] = sorted(set(stored_values + values_from_sheet))  

    return render_template("index.html", columns=columns, dropdown_options=dropdown_options, data=data)

@app.route("/report", methods=["GET"])
def report():
    try:
        # Fetch data from Google Sheets
        data = sheet.get_all_records()
        df = pd.DataFrame(data)

        if df.empty:
            return "<h2>No data available to generate the report.</h2><a href='/'>Go Back</a>"

        # Ensure required columns exist
        required_columns = [
            "Coach Code", "Schedule", "Secondary Suspension Type",
            "Type of Spring", "Type of Failure", "Location", "Reason for Failure"
        ]
        for col in required_columns:
            if col not in df.columns:
                df[col] = ""  # Add missing columns with empty values to prevent errors

        # Get filter values from request arguments
        filters = {
            "Coach Code": request.args.get("Coach Code"),
            "Schedule": request.args.get("Schedule"),
            "Secondary Suspension Type": request.args.get("Secondary Suspension Type"),
            "Type of Spring": request.args.get("Type of Spring"),
            "Type of Failure": request.args.get("Type of Failure"),
            "Location": request.args.get("Location"),
            "Reason for Failure": request.args.get("Reason for Failure")
        }

        # Apply filters dynamically
        for column, value in filters.items():
            if value and value in df[column].values:
                df = df[df[column] == value]

        if df.empty:
            return "<h2>No data available after filtering.</h2><a href='/'>Go Back</a>"

        # Convert entire filtered DataFrame to an HTML table
        filtered_table_html = df.to_html(index=False, classes="filtered-data-table", border=1)

        # Count failures by type
        failure_counts = df["Type of Failure"].value_counts()

        # Convert to Summary Table
        summary_html = failure_counts.reset_index().to_html(index=False, classes="summary-table", border=1)

        # Generate Chart
        plt.figure(figsize=(8, 5))
        failure_counts.plot(kind="bar", color="skyblue", edgecolor="black")
        plt.xlabel("Type of Failure")
        plt.ylabel("Count")
        plt.title("Filtered Failure Type Distribution")
        plt.xticks(rotation=45)

        # Convert plot to PNG image
        img = io.BytesIO()
        plt.savefig(img, format="png")
        img.seek(0)
        plt.close()
        chart_url = base64.b64encode(img.getvalue()).decode()

        # Load dropdown options dynamically
        dropdown_options = {col: sorted(df[col].dropna().astype(str).unique().tolist()) for col in required_columns}

        return render_template("report.html", chart_url=chart_url, summary_html=summary_html, dropdown_options=dropdown_options, filtered_table_html=filtered_table_html)

    except Exception as e:
        return f"<h2>Error Occurred: {str(e)}</h2><a href='/'>Go Back</a>"

@app.route("/delete_entry", methods=["POST"])
def delete_entry():
    unique_id = request.json.get("unique_id")
    identifier_column = request.json.get("identifier_column")

    if not unique_id or not identifier_column:
        return jsonify({"success": False, "error": "Invalid identifier"})

    data = sheet.get_all_records()
    df = pd.DataFrame(data)

    if identifier_column not in df.columns:
        return jsonify({"success": False, "error": "Invalid column"})

    # Find the row index to delete
    row_index = df.index[df[identifier_column] == unique_id].tolist()

    if not row_index:
        return jsonify({"success": False, "error": "Entry not found"})

    sheet.delete_rows(row_index[0] + 2)  # Google Sheets uses 1-based index + header row

    return jsonify({"success": True, "message": f"Entry with {identifier_column}: {unique_id} deleted successfully!"})

@app.route("/add_column", methods=["POST"])
def add_column():
    new_column = request.json.get("column_name")
    
    if not new_column:
        return jsonify({"success": False, "error": "Invalid column name"})
    
    columns = sheet.row_values(1)
    if new_column not in columns:
        columns.append(new_column)
        sheet.insert_row(columns, 1)
    
    return jsonify({"success": True, "message": f"Column '{new_column}' added successfully!"})
@app.route("/add_value", methods=["POST"])
def add_value():
    new_value = request.json.get("value")
    dropdown_type = request.json.get("dropdown")

    if not new_value or not dropdown_type:
        return jsonify({"success": False, "error": "Invalid input"})

    if dropdown_type not in stored_options:
        stored_options[dropdown_type] = []

    if new_value not in stored_options[dropdown_type]:
        stored_options[dropdown_type].append(new_value)
        with open(OPTIONS_FILE, "w") as f:
            json.dump(stored_options, f, indent=4)

    return jsonify({"success": True, "message": f"Value '{new_value}' added successfully!"})

@app.route("/delete_value", methods=["POST"])
def delete_value():
    value_to_delete = request.json.get("value")
    dropdown_type = request.json.get("dropdown")

    if not value_to_delete or not dropdown_type:
        return jsonify({"success": False, "error": "Invalid input"})

    if dropdown_type in stored_options and value_to_delete in stored_options[dropdown_type]:
        stored_options[dropdown_type].remove(value_to_delete)
        with open(OPTIONS_FILE, "w") as f:
            json.dump(stored_options, f, indent=4)

    return jsonify({"success": True, "message": f"Value '{value_to_delete}' removed successfully!"})

if __name__ == "__main__":
    app.run(debug=True)
