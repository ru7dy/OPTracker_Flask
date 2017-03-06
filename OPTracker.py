from flask import Flask, render_template
app = Flask(__name__)

@app.route('/')
def index():
  return render_template('index.html')

@app.route('/estimate', methods=['POST'])
def estimate():
  return render_template('estimate.html')
