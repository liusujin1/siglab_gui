  function x=s2n(s)
% function x=s2n(s)
% String to Number convertor,
% input string in s, number returned in x if successful,
% [] returned on failure.
% Dick Benson, DSP Technology

% Far less glorified than MATLAB str2num.m (I don't see why it is so complex)
  if strcmp(s,'error') || strcmp(s,'s') 
    x=[];   % errors return the null 
  else
    x=eval(s,'s2n(''error'')');
  end; 
% end function 
