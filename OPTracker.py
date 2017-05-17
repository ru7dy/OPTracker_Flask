from flask import Flask, render_template, request
from analyze import estimate

app = Flask(__name__)

@app.route('/')
def index():
  return render_template('index.html')

@app.route('/estimate', methods=['POST'])
def query_results():
  # estimate()
  print(request.form['receipt_num'])
  return render_template('estimate.html')
