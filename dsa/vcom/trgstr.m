  function Out = trgstr(Action,Index);
% function Out = trgstr(Action,Index);
% Returns vector of strings for trigger theshold selector
% or the real number value for trigger level given an index input.
% Dick Benson, DSP Technology 
  Nlev = 32;      % 32 levels for hardware
  rt2  = sqrt(2); % hardware full scale is sqrt(2) 
  Nmax = round(0.7*Nlev/(2*rt2)); % don't go by 70 percent 
  tvec= (Nmax:-1:-Nmax)*100*rt2/(Nlev/2); % legit thresholds 
  if strcmp(Action,'list'),
     Out =[int2str(round(tvec(1))),'%'];
     for i=2:(2*Nmax+1)
        Out=[Out,'|',int2str(round(tvec(i))),'%']; 
     end;
  elseif strcmp(Action,'value'), 
     Out = tvec(Index);
  else
     disp([Action,' not recognized in trgstr.m']); 
  end; 
% end function






