.bottom-tab {
  position: fixed;
  bottom: 0;
  width: 100%;
  backdrop-filter: blur(12px);
  background: rgba(255, 255, 255, 0.1);
  box-shadow: 0 -2px 20px rgba(0, 255, 255, 0.2);
  border-top: 1px solid rgba(255, 255, 255, 0.3);
  z-index: 999;
  display: flex;
  justify-content: center;
}

.tab-inner {
  display: flex;
  width: 100%;
  max-width: 480px;
  justify-content: space-around;
  padding: 8px 0;
}

.tab-link, .logout-form button {
  color: #00ffff;
  text-decoration: none;
  font-size: 12px;
  text-align: center;
  transition: transform 0.2s ease, color 0.2s ease;
  display: flex;
  flex-direction: column;
  align-items: center;
  background: none;
  border: none;
  cursor: pointer;
}

.tab-link:hover, .logout-form button:hover {
  color: #ffffff;
  transform: translateY(-4px);
}

.tab-link .icon, .logout-form button .icon {
  font-size: 20px;
  display: block;
  animation: glow 1.5s infinite alternate;
}

.tab-link .label, .logout-form button .label {
  display: block;
  margin-top: 2px;
}

@keyframes glow {
  0% { text-shadow: 0 0 4px #00ffff, 0 0 8px #00ffff; }
  50% { text-shadow: 0 0 8px #00ffff, 0 0 16px #00ffff; }
  100% { text-shadow: 0 0 4px #00ffff, 0 0 8px #00ffff; }
}
