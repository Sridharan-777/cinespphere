// CineSphere Interactive Core
document.addEventListener("DOMContentLoaded", () => {
    // Auto-dismiss flash alerts after 6 seconds
    const alerts = document.querySelectorAll(".flash-alert");
    alerts.forEach(alert => {
        setTimeout(() => {
            alert.style.opacity = '0';
            alert.style.transition = 'opacity 0.4s ease';
            setTimeout(() => alert.remove(), 400);
        }, 6000);
    });
});
