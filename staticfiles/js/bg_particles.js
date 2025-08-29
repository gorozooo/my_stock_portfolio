const canvas = document.getElementById('bgCanvas');
const ctx = canvas.getContext('2d');
let width = canvas.width = window.innerWidth;
let height = canvas.height = window.innerHeight;

const particles = [];
const particleCount = 80;

class Particle {
  constructor() {
    this.x = Math.random() * width;
    this.y = Math.random() * height;
    this.vx = (Math.random()-0.5) * 0.5;
    this.vy = (Math.random()-0.5) * 0.5;
    this.size = 2 + Math.random()*2;
  }
  move() {
    this.x += this.vx;
    this.y += this.vy;
    if(this.x<0||this.x>width) this.vx*=-1;
    if(this.y<0||this.y>height) this.vy*=-1;
  }
  draw() {
    ctx.beginPath();
    ctx.arc(this.x,this.y,this.size,0,Math.PI*2);
    ctx.fillStyle = 'rgba(0,255,255,0.6)';
    ctx.fill();
  }
}

for(let i=0;i<particleCount;i++){
  particles.push(new Particle());
}

function animate(){
  ctx.clearRect(0,0,width,height);
  for(let i=0;i<particles.length;i++){
    particles[i].move();
    particles[i].draw();
    for(let j=i+1;j<particles.length;j++){
      const dx = particles[i].x - particles[j].x;
      const dy = particles[i].y - particles[j].y;
      const dist = Math.sqrt(dx*dx + dy*dy);
      if(dist < 120){
        ctx.beginPath();
        ctx.strokeStyle = `rgba(0,255,255,${0.2*(1-dist/120)})`;
        ctx.moveTo(particles[i].x, particles[i].y);
        ctx.lineTo(particles[j].x, particles[j].y);
        ctx.stroke();
      }
    }
  }
  requestAnimationFrame(animate);
}

window.addEventListener('resize', ()=>{
  width = canvas.width = window.innerWidth;
  height = canvas.height = window.innerHeight;
});
