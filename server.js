const { spawn } = require('child_process');
const http = require('http');

console.log('[Runner] Starting CineSphere Flask application on port 3000...');

function startFlask() {
  const env = { ...process.env, PORT: '3000', PYTHONPATH: '/app/applet' };
  const flask = spawn('python3', ['/app/applet/cinesphere/run.py'], {
    env,
    stdio: 'inherit',
    cwd: '/app/applet'
  });

  flask.on('close', (code) => {
    console.log(`[Runner] CineSphere process exited with code ${code}. Restarting in 1s...`);
    setTimeout(startFlask, 1000);
  });
}

startFlask();
