  function sout=volt2str(v,res)
% function sout=volt2str(v,res)
% Return formatted string with xxV units
% Dick Benson, DSP Technology
if strcmp(res,'hi'),
    if abs(v) < 1e-6, 
       sout = sprintf('%5.1fnV',v*1e9 ); 
    elseif abs(v) < 1e-3, 
        sout = sprintf('%5.1fuV',v*1e6 ); 
    elseif abs(v) < 1, 
        sout = sprintf('%5.1fmV',v*1e3 ); 
    elseif abs(v) < 1000, 
         sout = sprintf('%5.1fV',v ); 
    elseif abs(v) < 1e6
         sout = sprintf('%5.1fKV',v/1000); 
    else
        sout = sprintf('%7.1fV',v); 
    end; % if
elseif strcmp(res,'lo'),
    if abs(v) < 1e-6, 
       sout = sprintf('%3.0fnV',v*1e9 ); 
    elseif abs(v) < 1e-3, 
        sout = sprintf('%3.0fuV',v*1e6 ); 
    elseif abs(v) < 0.1, 
        sout = sprintf('%2.0fmV',v*1e3 );
    elseif abs(v) < 1, 
        sout = sprintf('%3.2fV',v );
    elseif abs(v) <= 10, 
        sout = sprintf('%3.1fV',v ); 
    elseif abs(v) < 1000, 
         sout = sprintf('%3.2fV',v ); 
    elseif abs(v) < 1e6
         sout = sprintf('%3.1fKV',v/1000); 
    else
        sout = sprintf('%7.1fV',v); 
    end; % 
else
     sout='error in volt2str';
end; 
%end function









