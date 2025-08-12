(function(){
  const toggle=document.getElementById('btnTheme');
  if(!toggle) return;
  const prefersDark=window.matchMedia('(prefers-color-scheme: dark)').matches;
  const stored=localStorage.getItem('theme');
  if(stored==='dark' || (!stored && prefersDark)){
    document.body.classList.add('dark');
  }
  toggle.addEventListener('click',()=>{
    document.body.classList.toggle('dark');
    const mode=document.body.classList.contains('dark')?'dark':'light';
    localStorage.setItem('theme',mode);
  });
})();

