// JS común del SaaS — auto-cerrar flashes después de 5s
document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("[role='alert'], .flash").forEach(el => {
    setTimeout(() => el.style.transition = "opacity .5s", 4000);
    setTimeout(() => el.style.opacity = "0", 4500);
    setTimeout(() => el.remove(), 5000);
  });
});
