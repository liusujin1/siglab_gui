  function sout=hz2str(f)
% function sout=hz2str(f) 
% Return a string with xHz  format
% Dick Benson, DSP Technology
  if abs(f) < 1e-3, 
       sout = sprintf('%4.2fuHz',f*1e6 ); 
  elseif abs(f) < 1,
       sout = sprintf('%3.3fHz',f); 
  elseif abs(f) < 10,
       sout = sprintf('%3.2fHz',f); 
  elseif abs(f) < 100,
       sout = sprintf('%3.1fHz',f); 
  elseif abs(f) <  1e3,
       sout = sprintf('%3.0fHz',f ); 
  elseif abs(f) < 1e4,
       sout = sprintf('%3.1fKHz',f*1e-3 ); 
  elseif abs(f) < 1e5,
       sout = sprintf('%3.1fKHz',f*1e-3 ); 
  elseif abs(f) < 1e6,
       sout = sprintf('%4.0fKHz',f*1e-3 ); 
  elseif abs(f) < 1e9,
       sout = sprintf('%4.0fMHz',f*1e-6 ); 
  else
       sout = sprintf('%9.2fHz',f ); 
  end; % if
% end hz2str function
