/* ==========================================================================
   StudyFlow · showcase site behaviour
   Progressive enhancement only: every section works with JS disabled.
   ========================================================================== */
(function () {
  "use strict";

  var root = document.documentElement;

  /* ---------------- theme ---------------- */

  var THEME_KEY = "studyflow-site-theme";

  function applyTheme(theme) {
    root.setAttribute("data-theme", theme);
    try {
      window.localStorage.setItem(THEME_KEY, theme);
    } catch (err) {
      /* storage unavailable — theme simply will not persist */
    }
  }

  try {
    var stored = window.localStorage.getItem(THEME_KEY);
    if (stored === "light" || stored === "dark") {
      root.setAttribute("data-theme", stored);
    }
  } catch (err) {
    /* ignore */
  }

  var themeToggle = document.getElementById("theme-toggle");
  if (themeToggle) {
    themeToggle.addEventListener("click", function () {
      var current = root.getAttribute("data-theme");
      if (current === "auto") {
        var prefersDark =
          window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
        current = prefersDark ? "dark" : "light";
      }
      applyTheme(current === "dark" ? "light" : "dark");
    });
  }

  /* ---------------- mobile nav ---------------- */

  var navToggle = document.getElementById("nav-toggle");
  var siteNav = document.getElementById("site-nav");

  if (navToggle && siteNav) {
    navToggle.addEventListener("click", function () {
      var open = siteNav.classList.toggle("is-open");
      navToggle.setAttribute("aria-expanded", open ? "true" : "false");
      navToggle.setAttribute("aria-label", open ? "关闭导航" : "打开导航");
    });

    siteNav.addEventListener("click", function (event) {
      if (event.target.tagName === "A") {
        siteNav.classList.remove("is-open");
        navToggle.setAttribute("aria-expanded", "false");
        navToggle.setAttribute("aria-label", "打开导航");
      }
    });
  }

  /* ---------------- sticky header shadow ---------------- */

  var header = document.getElementById("site-header");

  if (header) {
    var onScroll = function () {
      header.classList.toggle("is-scrolled", window.scrollY > 8);
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
  }

  /* ---------------- scroll spy ---------------- */

  var navLinks = Array.prototype.slice.call(
    document.querySelectorAll("#site-nav a[href^='#']")
  );
  var sections = navLinks
    .map(function (link) {
      return document.querySelector(link.getAttribute("href"));
    })
    .filter(Boolean);

  if (sections.length && "IntersectionObserver" in window) {
    var visible = new Set();

    var spy = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) {
            visible.add(entry.target.id);
          } else {
            visible.delete(entry.target.id);
          }
        });

        var activeId = null;
        for (var i = 0; i < sections.length; i += 1) {
          if (visible.has(sections[i].id)) {
            activeId = sections[i].id;
            break;
          }
        }

        navLinks.forEach(function (link) {
          link.classList.toggle("is-active", link.getAttribute("href") === "#" + activeId);
        });
      },
      { rootMargin: "-72px 0px -62% 0px", threshold: 0 }
    );

    sections.forEach(function (section) {
      spy.observe(section);
    });
  }

  /* ---------------- reveal on scroll ---------------- */

  var revealTargets = Array.prototype.slice.call(
    document.querySelectorAll(
      ".card, .role, .metric, .flow-step, .shot, .code-block, .compare-col, .table-wrap, .arch"
    )
  );

  if ("IntersectionObserver" in window && revealTargets.length) {
    revealTargets.forEach(function (el, index) {
      el.classList.add("reveal");
      el.style.transitionDelay = Math.min(index % 4, 3) * 55 + "ms";
    });

    var revealObserver = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) {
            entry.target.classList.add("is-visible");
            revealObserver.unobserve(entry.target);
          }
        });
      },
      { rootMargin: "0px 0px -8% 0px", threshold: 0.05 }
    );

    revealTargets.forEach(function (el) {
      revealObserver.observe(el);
    });
  }

  /* ---------------- copy to clipboard ---------------- */

  function flash(button, label) {
    var original = button.getAttribute("data-label") || button.textContent;
    button.setAttribute("data-label", original);
    button.textContent = label;
    button.disabled = true;
    window.setTimeout(function () {
      button.textContent = original;
      button.disabled = false;
    }, 1600);
  }

  Array.prototype.slice.call(document.querySelectorAll(".copy-btn")).forEach(function (button) {
    button.addEventListener("click", function () {
      var target = document.getElementById(button.getAttribute("data-copy-target"));
      if (!target) {
        return;
      }

      var text = target.innerText.replace(/\u00a0/g, " ");

      var done = function () {
        flash(button, "已复制");
      };

      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done, function () {
          fallbackCopy(text, done);
        });
      } else {
        fallbackCopy(text, done);
      }
    });
  });

  function fallbackCopy(text, done) {
    var area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    try {
      document.execCommand("copy");
      done();
    } catch (err) {
      /* ignore — copy is a convenience, not a requirement */
    }
    document.body.removeChild(area);
  }

  /* ---------------- screenshot lightbox ---------------- */

  var lightbox = document.getElementById("lightbox");
  var lightboxImg = document.getElementById("lightbox-img");
  var lightboxCaption = document.getElementById("lightbox-caption");

  function openLightbox(src, caption) {
    if (!lightbox) {
      return;
    }
    lightboxImg.src = src;
    lightboxImg.alt = caption || "";
    lightboxCaption.textContent = caption || "";
    lightbox.hidden = false;
    document.body.style.overflow = "hidden";
  }

  function closeLightbox() {
    if (!lightbox) {
      return;
    }
    lightbox.hidden = true;
    lightboxImg.src = "";
    document.body.style.overflow = "";
  }

  Array.prototype.slice.call(document.querySelectorAll(".shot")).forEach(function (figure) {
    figure.addEventListener("click", function () {
      openLightbox(figure.getAttribute("data-shot"), figure.getAttribute("data-caption"));
    });
  });

  if (lightbox) {
    lightbox.addEventListener("click", function (event) {
      if (
        event.target === lightbox ||
        (event.target.classList && event.target.classList.contains("lightbox-close"))
      ) {
        closeLightbox();
      }
    });
  }

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && lightbox && !lightbox.hidden) {
      closeLightbox();
    }
  });
})();
