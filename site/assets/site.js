// LULC Fetch website: shared by every page
(function () {
  // the phone menu
  const nav = document.getElementById("top"), toggle = nav && nav.querySelector(".menu-toggle");
  if (toggle) {
    toggle.addEventListener("click", () => { const on = nav.classList.toggle("open"); toggle.setAttribute("aria-expanded", on); });
    nav.querySelectorAll(".links a").forEach((a) => a.addEventListener("click", () => { nav.classList.remove("open"); toggle.setAttribute("aria-expanded", false); }));
  }
  // highlight the visitor's system and point the main button at its download (home page)
  const main = document.getElementById("cta-main"), label = document.getElementById("cta-label");
  if (main && label) {
    const ua = navigator.userAgent, win = /Windows/i.test(ua), mac = /Macintosh|Mac OS X/i.test(ua) && !/iPhone|iPad/i.test(ua);
    if (win) { label.textContent = "Download for Windows"; main.href = "https://github.com/agritechixr/lulc-fetch/releases/latest/download/LULC-Fetch-Windows.zip"; document.getElementById("dl-win")?.classList.add("hl"); }
    else if (mac) { label.textContent = "Download for Mac"; main.href = "https://github.com/agritechixr/lulc-fetch/releases/latest/download/LULC-Fetch-macOS-AppleSilicon.dmg"; document.getElementById("dl-mac")?.classList.add("hl"); }
  }
  // click a screenshot to see it large
  const lb = document.getElementById("lb");
  if (lb) {
    document.querySelectorAll(".shot, .frame img").forEach((img) => img.addEventListener("click", () => { lb.querySelector("img").src = img.src; lb.classList.add("open"); }));
    lb.addEventListener("click", () => lb.classList.remove("open"));
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") lb.classList.remove("open"); });
  }
})();
