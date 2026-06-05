function toggleFoldout(id) {
    const body = document.getElementById('foldout-body-' + id);
    const arrow = document.getElementById('foldout-arrow-' + id);
    const foldout = document.getElementById('foldout-' + id);

    body.classList.toggle('open');
    arrow.classList.toggle('open');
    foldout.classList.toggle('active');
}

function openFoldout(id) {
    const body = document.getElementById('foldout-body-' + id);
    const arrow = document.getElementById('foldout-arrow-' + id);
    const foldout = document.getElementById('foldout-' + id);
    if (body && !body.classList.contains('open')) {
        body.classList.add('open');
        arrow.classList.add('open');
        foldout.classList.add('active');
    }
}

window.addEventListener('DOMContentLoaded', function () {
    var hash = window.location.hash;
    if (!hash) return;
    var target = document.querySelector(hash);
    if (!target) return;

    var foldout = target.closest('.method-foldout');
    if (foldout) {
        var foldoutId = foldout.id.replace('foldout-', '');
        openFoldout(foldoutId);
        setTimeout(function () {
            target.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }, 300);
    }
});
