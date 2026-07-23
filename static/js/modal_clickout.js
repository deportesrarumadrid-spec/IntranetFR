/**
 * modal_clickout.js
 * Cierra cualquier modal al hacer clic en el backdrop,
 * SOLO si el usuario no ha modificado ningún campo dentro.
 */
(function () {
    'use strict';

    var _dirty = new WeakSet();

    // Marca el modal como "sucio" cuando el usuario cambia algún campo
    function _getModal(el) {
        return el.closest('[id*="modal"],[id*="Modal"],.modal,.modal-backdrop,.fisio-modal,.rrss-modal,.arch-modal');
    }
    document.addEventListener('input',  function (e) { var m = _getModal(e.target); if (m) _dirty.add(m); }, true);
    document.addEventListener('change', function (e) { var m = _getModal(e.target); if (m) _dirty.add(m); }, true);

    // Cierra el modal al clic en el backdrop (e.target ES el propio modal, no un hijo)
    document.addEventListener('click', function (e) {
        var el = e.target;
        // Excluir elementos interactivos — nunca son backdrops
        var tag = el.tagName ? el.tagName.toLowerCase() : '';
        if (tag === 'button' || tag === 'a' || tag === 'input' || tag === 'select' ||
            tag === 'textarea' || tag === 'label' || tag === 'span' || tag === 'i' ||
            tag === 'svg' || tag === 'path' || tag === 'img') return;
        // Solo actuar si el clic fue directo sobre el elemento modal (backdrop)
        var isBackdrop = (el.id && el.id.toLowerCase().indexOf('modal') !== -1) ||
                         (typeof el.className === 'string' &&
                          /\bmodal\b|\bmodal-backdrop\b|\bmodal-crono\b|\bmodal-ia\b|\bfisio-modal\b|\brrss-modal\b/.test(el.className));
        if (!isBackdrop) return;
        // Debe estar visible
        if (el.style.display === 'none') return;
        // No cerrar si el usuario ha editado algo dentro
        if (_dirty.has(el)) return;
        // Respetar data-no-clickout="1" para modales que no deben cerrarse así
        if (el.dataset && el.dataset.noClickout) return;
        el.style.display = 'none';
    });

    // Limpia el estado dirty cuando el modal se oculta (para que al reabrirlo empiece limpio)
    if (typeof MutationObserver !== 'undefined') {
        var obs = new MutationObserver(function (muts) {
            muts.forEach(function (m) {
                if (m.attributeName === 'style' &&
                    window.getComputedStyle(m.target).display === 'none') {
                    _dirty.delete(m.target);
                }
            });
        });
        function _observeAll() {
            document.querySelectorAll('[id*="modal"],[id*="Modal"],.modal,.modal-backdrop').forEach(function (el) {
                obs.observe(el, { attributes: true, attributeFilter: ['style'] });
            });
        }
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', _observeAll);
        } else {
            _observeAll();
        }
    }
})();
