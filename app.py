import os

from flask import Flask, render_template, request, redirect
from dotenv import load_dotenv
load_dotenv()
from modules.firebase_client import FirebaseClient

FirebaseClient.initialize("key.json")


from modules.admin_routes import bp as admin_bp
from modules.partner.partner_routes import bp as partner_bp
from modules.employee.employee_routes import bp as employee_bp
from modules.employee.employee_sales_routes import bp as employee_sales_bp


app = Flask(__name__)


app.secret_key = os.environ["FLASK_SECRET_KEY"]


app.register_blueprint(admin_bp)

app.register_blueprint(partner_bp)

app.register_blueprint(employee_bp)
app.register_blueprint(employee_sales_bp)

@app.route("/")
def home():
     return render_template("home.html")


@app.route("/become-a-partner", methods=["GET", "POST"])
def become_a_partner():
    if request.method == "POST":
        return redirect("/become-a-partner?submitted=1")

    success = request.args.get("submitted") == "1"
    return render_template("become_a_partner.html", success=success, error=None)


@app.route("/privacy-policy")
def privacy_policy():
    return render_template("privacy_policy.html")


@app.errorhandler(500)
def internal_error(e):
    return render_template(
        "error.html",
        error_message="Internal server error"
    ), 500


if __name__ == "__main__":
    app.run(debug=True)
