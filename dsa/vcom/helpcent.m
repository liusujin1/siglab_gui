% helpcent.m - opens the siglab help center
   [drv pa] = pathfind('vfg');
   siglab('exe',[drv strrep(pa,'vfg','doc') '\helpcent.pdf']);
