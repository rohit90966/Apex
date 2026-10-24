import React from 'react';
import ReactDOM from 'react-dom';
import App from './App.jsx';

const container = document.getElementById('root');

if (ReactDOM.createRoot) {
  const root = ReactDOM.createRoot(container);
  root.render(<App />);
} else {
  ReactDOM.render(<App />, container);
}
