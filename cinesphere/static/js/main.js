// CineSphere Interactive Core
document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("[data-background-image]").forEach(element => {
        const imageUrl = element.dataset.backgroundImage;
        if (imageUrl) {
            element.style.backgroundImage = `url(${JSON.stringify(imageUrl)})`;
        }
    });

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
