const { spawn } = require('child_process');
const path = require('path');

const projectRoot = __dirname;
const defaultPython = process.platform === 'win32'
  ? path.join(projectRoot, 'venv', 'Scripts', 'python.exe')
  : 'python3';
const python = process.env.PYTHON_BIN || defaultPython;
const port = process.env.PORT || '3000';

console.log(`[Runner] Starting CineSphere Flask application on port ${port}...`);

const flask = spawn(python, ['-m', 'cinesphere.run'], {
  env: { ...process.env, PORT: port, PYTHONPATH: projectRoot },
  stdio: 'inherit',
  cwd: projectRoot
});

flask.on('error', error => {
  console.error(`[Runner] Unable to start Python at ${python}: ${error.message}`);
  process.exit(1);
});

flask.on('close', code => process.exit(code ?? 1));
